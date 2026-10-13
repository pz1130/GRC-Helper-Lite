"""双通道关系推断；骨架照抄 M5 硬化后的 mapping/tasks.py。

继承而来的六条，均有出处，不重新踩：检查点查完立刻提交（advisory 锁不得
跨越 LLM 调用）、ProviderError 计入 failed 并继续、引用逐条丢弃而非整批
作废、按批原子提交、schema 带 maxItems、总时限与 <think> 剥离在 runner 内。
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.controls.models import Control, RelationType, active_controls
from app.db import session_factory
from app.errors import AppError
from app.llm.models import LLMCall
from app.llm.providers.base import ProviderError
from app.llm.routing import routed_model
from app.llm.runner import run
from app.llm.validation import ValidationFailure
from app.relations import params as relation_params
from app.relations.citations import RelationCitationValidator
from app.relations.clustering import batch_pairs, duplicate_pairs, section_clusters
from app.relations.prompts import (
    DEPENDS_SYSTEM,
    DUPLICATE_SYSTEM,
    RELATION_SCHEMA,
    RELATION_TASK_KEY,
    control_chars,
    render_cluster,
    render_pairs,
)
from app.review import service as review_service
from app.review.models import ProposalKind

logger = logging.getLogger(__name__)
CHECKPOINT_KEY = "_relation_inference"


@dataclass(frozen=True)
class Batch:
    system: str
    prompt: str
    control_ids: list[int]
    relation_type: str
    # 仅 duplicates 通道有：这一批实际并排给出的候选对，供闸 3 逐对核。
    # depends_on 是整簇一起判的，簇内任意两条都是合法组合，没有对可核。
    allowed_pairs: tuple[frozenset[int], ...] | None = field(default=None)


def relation_key(entry: dict[str, Any], relation_type: str) -> tuple[int, int, str]:
    """一条关系的身份。duplicates 对称，与 materialize 用同一条规范化规则。"""
    start, end = entry["from_control_id"], entry["to_control_id"]
    if relation_type == RelationType.DUPLICATES.value:
        start, end = min(start, end), max(start, end)
    return start, end, relation_type


def fingerprint(system: str, prompt: str, run_key: str | None, *, model: str | None = None) -> str:
    encoded = json.dumps(
        ["v1", run_key, model, system, RELATION_SCHEMA, prompt], sort_keys=True, ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _checkpoint(session: AsyncSession, key: str) -> LLMCall | None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(bytes.fromhex(key)[:8], "big", signed=True)},
    )
    return await session.scalar(
        select(LLMCall)
        .where(
            LLMCall.task_key == RELATION_TASK_KEY,
            LLMCall.status == "ok",
            LLMCall.redaction_hits.contains({CHECKPOINT_KEY: {"fingerprint": key}}),
        )
        .order_by(LLMCall.id.desc())
        .limit(1)
    )


async def _build_batches(session: AsyncSession) -> list[Batch]:
    controls = {c.id: c for c in await session.scalars(active_controls().order_by(Control.id))}
    if not controls:
        raise AppError("控制点库为空，无法推断关系；请先在确认队列中确认控制点", code="relation.no_controls")

    # 字符预算按控制点在 prompt 里的实际渲染长度累加，两条通道共用同一把尺。
    sizes = {control_id: control_chars(c) for control_id, c in controls.items()}
    # 这些值按语料标定，来自 AppSetting——写死在常量里换个客户就得改代码部署。
    cfg = await relation_params.load(session)
    batches: list[Batch] = []

    pairs = await duplicate_pairs(session, top_k=cfg.top_k, min_similarity=cfg.min_similarity)
    for window in batch_pairs(pairs, sizes=sizes, max_pairs=cfg.pairs_per_batch,
                              max_chars=cfg.max_batch_chars):
        rendered = render_pairs(
            [(controls[p.low], controls[p.high], p.similarity) for p in window]
        )
        batches.append(Batch(
            system=DUPLICATE_SYSTEM,
            prompt=rendered,
            control_ids=sorted({i for p in window for i in (p.low, p.high)}),
            relation_type=RelationType.DUPLICATES.value,
            allowed_pairs=tuple(frozenset((p.low, p.high)) for p in window),
        ))

    for cluster in await section_clusters(
        session, sizes=sizes, max_size=cfg.max_cluster_size,
        overlap=cfg.cluster_overlap, max_chars=cfg.max_batch_chars,
    ):
        batches.append(Batch(
            system=DEPENDS_SYSTEM,
            prompt=render_cluster([controls[i] for i in cluster.control_ids]),
            control_ids=list(cluster.control_ids),
            relation_type=RelationType.DEPENDS_ON.value,
        ))

    if not batches:
        raise AppError("没有可推断的候选：控制点缺少向量或章节内不足两条", code="relation.no_candidates")
    return batches


async def run_inference(
    session: AsyncSession, *, run_key: str | None = None
) -> dict[str, Any]:
    batches = await _build_batches(session)
    summary: dict[str, Any] = {
        "batches": len(batches),
        "proposals": 0,
        "rejected": 0,
        "failed": 0,
        "dropped_relations": 0,
        "duplicate_relations": 0,
        "completed_batches": 0,
        "skipped_batches": 0,
        "attempted_batches": 0,
        "proposal_ids": [],
        "llm_call_ids": [],
        "resumed_proposal_ids": [],
    }

    # 章节簇是带重叠切分的，重叠区里的每一对会在两个批次各判一次；两条通道
    # 之间也可能撞上同一对。不去重就会产出两条一模一样的待确认提案，审核者
    # 确认了第一条，第二条只会在 materialize 里撞 Conflict，只能手工拒绝。
    seen: set[tuple[int, int, str]] = set()

    for batch in batches:
        prompt = batch.prompt + "\nOutput JSON Schema:\n" + json.dumps(RELATION_SCHEMA)
        key = fingerprint(
            batch.system, prompt, run_key, model=await routed_model(session, RELATION_TASK_KEY),
        )
        cached = await _checkpoint(session, key)
        resumed = (
            cached.redaction_hits[CHECKPOINT_KEY]["proposal_ids"]
            if cached is not None
            else None
        )
        # 检查点查完立刻提交：advisory 锁是事务级的，不提交就跨越整个 LLM 调用。
        await session.commit()
        if resumed is not None:
            summary["skipped_batches"] += 1
            summary["resumed_proposal_ids"].extend(resumed)
            continue

        validator = RelationCitationValidator(
            session, control_ids=batch.control_ids, allowed_pairs=batch.allowed_pairs
        )
        summary["attempted_batches"] += 1
        try:
            # 引用校验不进 run()：runner 只对 schema 失败做纠错重试，引用失败
            # 直接抛出，移到这里逐条判可把「一条不合格整批作废」变成「只丢那一条」。
            result = await run(
                session,
                task_key=RELATION_TASK_KEY,
                system=batch.system,
                prompt=prompt,
                schema=RELATION_SCHEMA,
            )
        except ValidationFailure as failure:
            logger.warning("Relation batch rejected: %s", failure.reason)
            summary["rejected"] += 1
            await session.commit()
            continue
        except ProviderError as failure:
            logger.warning("Relation batch failed: %s", failure)
            summary["failed"] += 1
            await session.commit()
            continue
        except Exception:
            await session.commit()
            raise

        call = await session.get(LLMCall, result.llm_call_id)
        if call is None:
            raise RuntimeError("Relation runner did not persist its LLMCall")

        entries = result.payload["relations"]
        if entries and await validator.check(result.payload) is not None:
            kept = []
            for entry in entries:
                reason = await validator.check({"relations": [entry]})
                if reason is None:
                    kept.append(entry)
                else:
                    logger.warning("Dropped one relation: %s", reason)
                    summary["dropped_relations"] += 1
            entries = kept

        unique = []
        for entry in entries:
            marker = relation_key(entry, batch.relation_type)
            if marker in seen:
                logger.info("Skipped a relation already proposed this run: %s", marker)
                summary["duplicate_relations"] += 1
                continue
            seen.add(marker)
            unique.append(entry)
        entries = unique

        ids: list[int] = []
        try:
            async with session.begin_nested():
                for entry in entries:
                    payload = {**entry, "relation_type": batch.relation_type}
                    proposal = await review_service.create(
                        session,
                        kind=ProposalKind.RELATION,
                        payload=payload,
                        citations=[
                            {"control_id": entry["from_control_id"],
                             "quote": entry["from_quote"]},
                            {"control_id": entry["to_control_id"],
                             "quote": entry["to_quote"]},
                        ],
                        confidence=entry["confidence"],
                        llm_call_id=result.llm_call_id,
                    )
                    await session.flush()
                    ids.append(proposal.id)
                call.redaction_hits = {
                    **(call.redaction_hits or {}),
                    CHECKPOINT_KEY: {"fingerprint": key, "proposal_ids": ids},
                }
                await session.flush()
        except Exception:
            await session.commit()
            raise

        await session.commit()
        summary["completed_batches"] += 1
        summary["proposals"] += len(ids)
        summary["proposal_ids"].extend(ids)
        summary["llm_call_ids"].append(result.llm_call_id)
    return summary


async def infer_relations(ctx: dict[str, Any]) -> dict[str, Any]:
    async with session_factory() as session:
    # 检查点身份 = 输入 + 模型。原来入口把每次投递的 job_id 当 run_key 传进来，
    # 用户每点一次，同样的批次就重新付费调一次模型、再出一整套重复提案。
    # run_key 只留给评估脚本：它们要的正是「同样输入再跑一遍」。
        return await run_inference(session)
