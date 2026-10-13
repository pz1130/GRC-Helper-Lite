from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import EMBEDDING_DIM
from app.crypto import decrypt, encrypt, mask
from app.db import get_session
from app.decision.base import DecisionRequest
from app.decision.factory import build_decision_provider, supports_decisions
from app.decision.settings import TASK_KEY as DECISION_TASK_KEY
from app.errors import AppError, BadRequest, Conflict, NotFound
from app.iam.audit import record
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.llm import budget
from app.llm.models import AppSetting, LLMCall, LLMProviderConfig, RedactionRule, TaskRouting
from app.llm.providers.base import (
    CompletionRequest,
    EmbeddingRequest,
    ProviderError,
)
from app.llm.providers.factory import build_provider
from app.llm.redaction import load_engine
from app.llm.schemas import (
    BulkRoutingIn,
    PreviewIn,
    PreviewOut,
    ProviderCreateIn,
    ProviderOut,
    ProviderUpdateIn,
    RedactionRuleIn,
    RedactionRuleOut,
    RoutingIn,
    RoutingOut,
    TaskSpecOut,
    ThresholdsIn,
    ThresholdsOut,
)
from app.llm.tasks import TASK_SPECS, TaskSpec, bulk_assignable_keys

router = APIRouter(prefix="/api/settings", tags=["settings"])

_THRESHOLD_DEFAULTS = {
    "auto_accept_threshold": 0.90,
    "force_manual_threshold": 0.60,
    "review_sample_rate": 0.10,
    "monthly_budget_usd": 200.0,
}
_THRESHOLD_KEYS = tuple(_THRESHOLD_DEFAULTS)


def _to_out(config: LLMProviderConfig) -> ProviderOut:
    return ProviderOut(
        id=config.id,
        name=config.name,
        kind=config.kind,
        model=config.model,
        base_url=config.base_url,
        enabled=config.enabled,
        is_fallback=config.is_fallback,
        monthly_budget=config.monthly_budget,
        api_key_masked=mask(decrypt(config.api_key_encrypted)),
        capabilities=["decision"] if supports_decisions(config.kind) else ["chat", "embedding"],
    )


def _snapshot(config: LLMProviderConfig) -> dict:
    # 刻意不含 api_key_encrypted——审计日志是永久的，密钥不许进去
    return {
        "name": config.name,
        "kind": config.kind.value,
        "model": config.model,
        "base_url": config.base_url,
        "enabled": config.enabled,
        "is_fallback": config.is_fallback,
    }


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(
    _: User = Depends(require(Permission.LLM_CONFIG_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> list[ProviderOut]:
    configs = await session.scalars(select(LLMProviderConfig).order_by(LLMProviderConfig.id))
    return [_to_out(c) for c in configs]


@router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def create_provider(
    payload: ProviderCreateIn,
    request: Request,
    actor: User = Depends(require(Permission.LLM_CONFIG_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> ProviderOut:
    if supports_decisions(payload.kind) and payload.is_fallback:
        raise BadRequest("判断模型不能作为聊天备用服务", code="decision.capability_mismatch")
    config = LLMProviderConfig(
        name=payload.name,
        kind=payload.kind,
        model=payload.model,
        base_url=payload.base_url,
        api_key_encrypted=encrypt(payload.api_key),
        is_fallback=payload.is_fallback,
        monthly_budget=payload.monthly_budget,
    )
    session.add(config)
    await session.flush()
    await record(
        session,
        user=actor,
        action="provider.create",
        entity_type="LLMProviderConfig",
        entity_id=config.id,
        after=_snapshot(config),
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return _to_out(config)


@router.patch("/providers/{config_id}", response_model=ProviderOut)
async def update_provider(
    config_id: int,
    payload: ProviderUpdateIn,
    request: Request,
    actor: User = Depends(require(Permission.LLM_CONFIG_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> ProviderOut:
    config = await session.get(LLMProviderConfig, config_id)
    if config is None:
        raise NotFound("provider 不存在", code="provider.not_found")

    if supports_decisions(config.kind) and payload.is_fallback:
        raise BadRequest("判断模型不能作为聊天备用服务", code="decision.capability_mismatch")
    before = _snapshot(config)
    data = payload.model_dump(exclude_unset=True)
    if "api_key" in data:
        config.api_key_encrypted = encrypt(data.pop("api_key"))
    for field, value in data.items():
        setattr(config, field, value)
    await session.flush()

    await record(
        session,
        user=actor,
        action="provider.update",
        entity_type="LLMProviderConfig",
        entity_id=config.id,
        before=before,
        after=_snapshot(config),
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return _to_out(config)


# Annotated 形式的依赖别名。本文件其余端点沿用旧的 `= Depends(...)` 默认值写法
# （ruff 的 B008 对它们都有告警），新写的端点用这两个别名，不再添新的告警。
ConfigWriter = Annotated[User, Depends(require(Permission.LLM_CONFIG_WRITE))]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.delete("/providers/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    config_id: int,
    request: Request,
    actor: ConfigWriter,
    session: Session,
) -> Response:
    """删掉一条 provider 配置。

    这张表此前只进不出：每跑一次 e2e 冒烟就留一行，开发库攒到过 33 条，最后只能
    写 SQL 清。它不像 `controls` 或 `users` 那样是审计证据——`llm_call` 指向它的
    外键是 `ON DELETE SET NULL`，删掉不会让任何一次调用记录指向空气，账单与用量
    仍然查得到。所以这里是真删，不是软删。

    **被任务路由指着的不能删**：那条外键是 `RESTRICT`，数据库本来就会拦，但那样
    抛出来的是一条 IntegrityError。这里先查一次，好把"哪几个任务还在用它"说清楚。
    """
    config = await session.get(LLMProviderConfig, config_id)
    if config is None:
        raise NotFound("provider 不存在", code="provider.not_found")

    bound = list(
        await session.scalars(
            select(TaskRouting.task_key)
            .where(TaskRouting.provider_config_id == config_id)
            .order_by(TaskRouting.task_key)
        )
    )
    if bound:
        raise Conflict(
            f"这些任务还在用它，请先改路由：{'、'.join(bound)}",
            code="provider.still_routed",
            # 传列表而不是拼好的串：顿号是中文排版，拼进参数就会出现在英文句子里。
            params={"tasks": list(bound)},
        )

    before = _snapshot(config)   # 同 create/update：刻意不含密钥
    await session.delete(config)
    await record(
        session,
        user=actor,
        action="provider.delete",
        entity_type="LLMProviderConfig",
        entity_id=config_id,
        before=before,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/providers/{config_id}/test")
async def test_provider(
    config_id: int,
    _: User = Depends(require(Permission.LLM_CONFIG_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    config = await session.get(LLMProviderConfig, config_id)
    if config is None:
        raise NotFound("provider 不存在", code="provider.not_found")

    # 一个 provider 配的可能是聊天模型，也可能是向量模型，两者接口不同。
    # 只探聊天会让 embedding provider 永远报错（实测 embo-01 打 chat 返回
    # 400 unknown model），管理员会误以为是 key 或网络的问题。
    # 所以两种能力都探，报出它实际能干什么。
    if supports_decisions(config.kind):
        try:
            await build_decision_provider(config).decide(DecisionRequest(
                state="This is a synthetic connectivity test.",
                questions={"probe": "Is this a connectivity test?"}, model=config.model,
            ))
        except Exception:  # noqa: BLE001 —— 探测不能把上游回显的密钥带回页面。
            return {"ok": False, "capability": "decision", "code": "unreachable"}
        return {"ok": True, "capability": "decision", "code": "decision_ok"}
    provider = build_provider(config)
    failures: dict[str, str] = {}

    try:
        await provider.complete(
            CompletionRequest(
                system="You are a connectivity probe.",
                prompt="Reply with the single word: ok",
                model=config.model,
                max_tokens=16,
            )
        )
    except ProviderError as exc:
        failures["chat"] = str(exc)
    else:
        return {"ok": True, "capability": "chat", "code": "chat_ok"}

    try:
        result = await provider.embed(
            EmbeddingRequest(texts=["connectivity probe"], model=config.model)
        )
    except ProviderError as exc:
        failures["embedding"] = str(exc)
    else:
        dim = len(result.vectors[0]) if result.vectors else 0
        if dim != EMBEDDING_DIM:
            # 维度对不上，写库时才炸就太晚了——在这里就说清楚
            return {
                "ok": False,
                "capability": "embedding",
                "code": "embedding_dimension_mismatch",
                "dimension": dim,
                "expected_dimension": EMBEDDING_DIM,
            }
        return {
            "ok": True,
            "capability": "embedding",
            "code": "embedding_ok",
            "dimension": dim,
        }

    return {"ok": False, "capability": None, "code": "unreachable", "failures": failures}


@router.get("/routing", response_model=list[RoutingOut])
async def list_routing(
    _: User = Depends(require(Permission.LLM_CONFIG_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> list[TaskRouting]:
    return list(await session.scalars(select(TaskRouting).order_by(TaskRouting.task_key)))


@router.get("/routing/tasks", response_model=list[TaskSpecOut])
async def list_tasks(
    _: User = Depends(require(Permission.LLM_CONFIG_WRITE)),
) -> list[TaskSpec]:
    """任务清单由后端给，前端不再自己维护一份——那份漂移过（见 llm/tasks.py）。"""
    return list(TASK_SPECS)


@router.put("/routing/bulk", response_model=list[RoutingOut])
async def bulk_upsert_routing(
    payload: BulkRoutingIn,
    actor: User = Depends(require(Permission.LLM_CONFIG_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> list[TaskRouting]:
    """把一个 provider 绑到该 capability 下所有已实现的任务上。

    只留**一条**审计（而不是每个任务一条）：这在管理员眼里是一个动作，
    审计日志应当照着人的动作记，否则查起来是九行噪声。绑定明细写进 after。
    """
    config = await session.get(LLMProviderConfig, payload.provider_config_id)
    if config is None:
        raise NotFound("provider 不存在", code="provider.not_found")

    if supports_decisions(config.kind):
        raise BadRequest("请通过独立的判断模型设置启用", code="decision.capability_mismatch")
    keys = bulk_assignable_keys(payload.capability)
    existing = {
        routing.task_key: routing
        for routing in await session.scalars(
            select(TaskRouting).where(TaskRouting.task_key.in_(keys))
        )
    }
    updated: list[TaskRouting] = []
    for key in keys:
        routing = existing.get(key)
        if routing is None:
            routing = TaskRouting(task_key=key)
            session.add(routing)
        routing.provider_config_id = payload.provider_config_id
        routing.temperature = payload.temperature
        routing.max_tokens = payload.max_tokens
        updated.append(routing)
    await session.flush()

    await record(
        session,
        user=actor,
        action="routing.bulk_upsert",
        entity_type="TaskRouting",
        entity_id=config.id,
        after={
            "capability": payload.capability,
            "provider_config_id": payload.provider_config_id,
            "task_keys": list(keys),
        },
    )
    await session.commit()
    return updated


@router.put("/routing", response_model=RoutingOut)
async def upsert_routing(
    payload: RoutingIn,
    actor: User = Depends(require(Permission.LLM_CONFIG_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> TaskRouting:
    config = await session.get(LLMProviderConfig, payload.provider_config_id)
    if config is None:
        raise NotFound("provider 不存在", code="provider.not_found")
    if payload.task_key == DECISION_TASK_KEY or supports_decisions(config.kind):
        raise BadRequest("请通过独立的判断模型设置启用", code="decision.capability_mismatch")
    routing = await session.scalar(
        select(TaskRouting).where(TaskRouting.task_key == payload.task_key)
    )
    if routing is None:
        routing = TaskRouting(task_key=payload.task_key)
        session.add(routing)
    routing.provider_config_id = payload.provider_config_id
    routing.temperature = payload.temperature
    routing.max_tokens = payload.max_tokens
    await session.flush()
    await record(
        session,
        user=actor,
        action="routing.upsert",
        entity_type="TaskRouting",
        entity_id=routing.id,
        after={"task_key": routing.task_key, "provider_config_id": routing.provider_config_id},
    )
    await session.commit()
    return routing


@router.get("/redaction", response_model=list[RedactionRuleOut])
async def list_rules(
    _: User = Depends(require(Permission.REDACTION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> list[RedactionRule]:
    return list(
        await session.scalars(
            select(RedactionRule).order_by(RedactionRule.ruleset, RedactionRule.order_index)
        )
    )


@router.post("/redaction", response_model=RedactionRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: RedactionRuleIn,
    actor: User = Depends(require(Permission.REDACTION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> RedactionRule:
    rule = RedactionRule(**payload.model_dump())
    session.add(rule)
    await session.flush()
    await record(
        session,
        user=actor,
        action="redaction.create",
        entity_type="RedactionRule",
        entity_id=rule.id,
        after=payload.model_dump(mode="json"),
    )
    await session.commit()
    return rule


@router.patch("/redaction/{rule_id}", response_model=RedactionRuleOut)
async def update_rule(
    rule_id: int,
    payload: RedactionRuleIn,
    actor: User = Depends(require(Permission.REDACTION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> RedactionRule:
    rule = await session.get(RedactionRule, rule_id)
    if rule is None:
        raise NotFound("脱敏规则不存在", code="redaction_rule.not_found")
    for field, value in payload.model_dump().items():
        setattr(rule, field, value)
    await session.flush()
    await record(
        session,
        user=actor,
        action="redaction.update",
        entity_type="RedactionRule",
        entity_id=rule.id,
        after=payload.model_dump(mode="json"),
    )
    await session.commit()
    return rule


@router.post("/redaction/preview", response_model=PreviewOut)
async def preview(
    payload: PreviewIn,
    _: User = Depends(require(Permission.REDACTION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> PreviewOut:
    """spec §6.2 的"发送预览"：让人看到实际会发出去什么。"""
    engine = await load_engine(session, payload.ruleset)
    result = engine.redact(payload.text)
    return PreviewOut(redacted=result.text, hits=result.hits)


@router.get("/thresholds", response_model=ThresholdsOut)
async def get_thresholds(
    _: User = Depends(require(Permission.LLM_CONFIG_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> ThresholdsOut:
    values: dict[str, float] = {}
    for key in _THRESHOLD_KEYS:
        setting = await session.get(AppSetting, key)
        values[key] = (
            float(setting.value["value"]) if setting else _THRESHOLD_DEFAULTS[key]
        )
    return ThresholdsOut(**values)


@router.put("/thresholds", response_model=ThresholdsOut)
async def put_thresholds(
    payload: ThresholdsIn,
    actor: User = Depends(require(Permission.LLM_CONFIG_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> ThresholdsOut:
    if payload.auto_accept_threshold <= payload.force_manual_threshold:
        raise AppError("自动接受阈值必须高于强制人工阈值", code="settings.threshold_order")

    for key in _THRESHOLD_KEYS:
        value = getattr(payload, key)
        setting = await session.get(AppSetting, key)
        if setting is None:
            session.add(AppSetting(key=key, value={"value": value}))
        else:
            setting.value = {"value": value}
    await session.flush()
    await record(
        session,
        user=actor,
        action="thresholds.update",
        entity_type="AppSetting",
        entity_id="thresholds",
        after=payload.model_dump(mode="json"),
    )
    await session.commit()
    return ThresholdsOut(**payload.model_dump())


@router.get("/usage")
async def usage(
    _: User = Depends(require(Permission.READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    setting = await session.get(AppSetting, "monthly_budget_usd")
    rows = await session.execute(
        select(LLMCall.task_key, func.sum(LLMCall.cost), func.count()).group_by(LLMCall.task_key)
    )
    return {
        "month_to_date_cost": await budget.month_to_date_cost(session),
        "budget": float(setting.value["value"]) if setting else None,
        # 大于 0 时上面那个花费是下限，不是实际值——界面必须说出来
        "uncosted_calls": await budget.uncosted_calls(session),
        "by_task": [
            {"task_key": task, "cost": float(cost or 0), "calls": count}
            for task, cost, count in rows
        ],
    }
