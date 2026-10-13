import pytest

from app.llm.budget import BudgetExceeded, check, month_to_date_cost
from app.crypto import encrypt
from app.llm.models import (
    AppSetting,
    LLMCall,
    LLMProviderConfig,
    ProviderKind,
    RulesetName,
)


async def _spend(db_session, amount: float, *, provider_config_id: int | None = None) -> None:
    db_session.add(
        LLMCall(
            provider_config_id=provider_config_id,
            model="claude-opus-5",
            task_key="control_extract",
            prompt_hash="h",
            cost=amount,
            ruleset=RulesetName.GENERATION,
            status="ok",
        )
    )
    await db_session.flush()


async def _provider(db_session, *, name: str, budget: float | None) -> LLMProviderConfig:
    config = LLMProviderConfig(
        name=name,
        kind=ProviderKind.ANTHROPIC,
        model="claude-opus-5",
        api_key_encrypted=encrypt("k"),
        monthly_budget=budget,
    )
    db_session.add(config)
    await db_session.flush()
    return config


async def _budget(db_session, amount: float) -> None:
    # 迁移 0004 已经种了 monthly_budget_usd=200，改那一行，别插重复主键。
    setting = await db_session.get(AppSetting, "monthly_budget_usd")
    if setting is None:
        db_session.add(AppSetting(key="monthly_budget_usd", value={"value": amount}))
    else:
        setting.value = {"value": amount}
    await db_session.flush()


@pytest.mark.asyncio
async def test_month_to_date_cost_sums_calls(db_session):
    await _spend(db_session, 1.5)
    await _spend(db_session, 2.5)
    assert await month_to_date_cost(db_session) == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_check_passes_below_budget(db_session):
    await _budget(db_session, 100.0)
    await _spend(db_session, 10.0)
    await check(db_session, interactive=False)   # 不抛异常即通过


@pytest.mark.asyncio
async def test_batch_task_blocked_at_full_budget(db_session):
    await _budget(db_session, 10.0)
    await _spend(db_session, 10.0)
    with pytest.raises(BudgetExceeded):
        await check(db_session, interactive=False)


@pytest.mark.asyncio
async def test_interactive_task_survives_full_budget(db_session):
    """spec §9：达 100% 暂停批量任务但保留交互式任务，避免系统完全瘫痪。"""
    await _budget(db_session, 10.0)
    await _spend(db_session, 10.0)
    await check(db_session, interactive=True)   # 不抛异常


@pytest.mark.asyncio
async def test_no_budget_configured_means_unlimited(db_session):
    # 0004 seeds a $200 cap; "no budget configured" requires that row gone.
    existing = await db_session.get(AppSetting, "monthly_budget_usd")
    if existing is not None:
        await db_session.delete(existing)
        await db_session.flush()
    await _spend(db_session, 9999.0)
    await check(db_session, interactive=False)


@pytest.mark.asyncio
async def test_provider_budget_is_actually_enforced(db_session):
    """provider 级预算曾经是个只存不看的死字段——设置页显示成开关但从不生效。"""
    config = await _provider(db_session, name="p", budget=5.0)
    await _spend(db_session, 5.0, provider_config_id=config.id)

    with pytest.raises(BudgetExceeded) as caught:
        await check(db_session, interactive=False, provider=config)
    # provider 级与全局各有各的 code：原来共用一个，把「provider「p」」这段中文当参数
    # 塞进英文句子开头。
    assert caught.value.code == "budget.provider_exhausted"
    assert caught.value.params == {"provider": "p", "spent": "5.00", "budget": "5.00"}


@pytest.mark.asyncio
async def test_provider_budget_counts_only_that_provider(db_session):
    config = await _provider(db_session, name="p", budget=5.0)
    other = await _provider(db_session, name="other", budget=None)
    await _spend(db_session, 100.0, provider_config_id=other.id)

    await check(db_session, interactive=False, provider=config)


@pytest.mark.asyncio
async def test_provider_without_budget_is_unlimited(db_session):
    await _budget(db_session, 0)   # 摘掉全局预算，单独验 provider 这一层
    config = await _provider(db_session, name="p", budget=None)
    await _spend(db_session, 9999.0, provider_config_id=config.id)

    await check(db_session, interactive=False, provider=config)


@pytest.mark.asyncio
async def test_provider_budget_still_yields_to_interactive(db_session):
    config = await _provider(db_session, name="p", budget=5.0)
    await _spend(db_session, 5.0, provider_config_id=config.id)

    await check(db_session, interactive=True, provider=config)


# ── 未计价的模型要可见 ── OQ-22 ────────────────────────────────────
#
# `pricing.py` 对未知模型返回 0 是**有意为之**（注释写了：成本统计不准可以接受，
# 让整条流水线挂掉不可接受）。问题不在这个决定，在于**没有任何信号说"这个模型
# 没有价"**：预算卡照常显示 $0.00 / $350.00，预算闸门形同虚设。
#
# 修法不是往价格表里加某个具体模型——价格会变，下一家机构又换模型，那是会过期
# 的数据。要让"未计价"这件事本身可见。


def test_unknown_model_is_reported_as_unpriced_not_as_free():
    from app.llm.pricing import estimate_cost, is_priced

    assert is_priced("claude-opus-5") is True
    assert is_priced("minimax-m3") is False
    # 仍然返回 0 而不是抛异常——这条行为不变
    assert estimate_cost("minimax-m3", 10_000, 10_000) == 0.0


@pytest.mark.asyncio
async def test_usage_counts_calls_that_were_never_priced(db_session):
    """用量接口要能回答"这个月有多少次调用根本没算进成本"。"""
    from app.llm import budget as budget_module

    for model, count in (("claude-opus-5", 2), ("minimax-m3", 3)):
        for index in range(count):
            db_session.add(
                LLMCall(
                    model=model,
                    task_key="control_extract",
                    prompt_hash=f"{model}-{index}",
                    tokens_in=100,
                    tokens_out=50,
                    cost=0.0,
                    latency_ms=10,
                    ruleset=RulesetName.GENERATION,
                    status="ok",
                    cost_unknown=model == "minimax-m3",
                )
            )
    await db_session.flush()

    assert await budget_module.uncosted_calls(db_session) == 3


def test_budget_exhaustion_is_an_app_error_with_a_code():
    """预算耗尽必须是带 code 的业务错误，不能是裸 Exception。

    原先它是 `class BudgetExceeded(Exception)`，全仓**没有任何地方捕获它**。
    批量任务里 `except Exception` 是 commit 后 re-raise，所以第一批就把整个任务
    打挂；而 `record_failed_batch`（能在确认队列里看见并重跑的那行）只对
    ValidationFailure 调用，预算这条路一行都不留。没有查任务状态的接口，
    `budget.check` 又在任何调用之前就抛——连 LLMCall 都没有。

    结果：用户点「抽取控制点」，界面说"已排队"，然后什么都没有。唯一的痕迹在
    worker 日志里。这和 OQ-22（异步失败从不呈现）是同一个形状，只是连"落库"都没有。
    """
    from app.errors import AppError
    from app.llm.budget import BudgetExceeded, _enforce

    assert issubclass(BudgetExceeded, AppError)

    try:
        _enforce(None, budget=10.0, spent=12.5, interactive=False)
    except BudgetExceeded as exc:
        assert exc.code == "budget.exhausted"
        assert exc.params["spent"] == "12.50"
        assert exc.params["budget"] == "10.00"
    else:
        raise AssertionError("超预算时必须抛出")

    # 交互式任务照旧放行（spec §9：只暂停批量）
    _enforce(None, budget=10.0, spent=12.5, interactive=True)
