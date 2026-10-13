"""按批映射并原子提交，骨架沿用 M4 的 extraction/tasks.py。"""

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.controls.models import Control, active_controls
from app.db import session_factory
from app.errors import AppError, NotFound
from app.frameworks.models import Framework, FrameworkItem
from app.llm.models import LLMCall
from app.llm.providers.base import ProviderError
from app.llm.routing import routed_model
from app.llm.runner import run
from app.llm.validation import ValidationFailure
from app.mapping import jev_prefilter
from app.mapping.batching import (
    build_batches,
    chunk_items,
    render_controls,
    render_controls_unchecked,
    render_items,
    split_to_fit,
)
from app.mapping.candidates import (
    TOP_K,
    control_text,
    item_text,
    load_vectors,
    rank_candidates,
)
from app.mapping.citations import MappingCitationValidator
from app.mapping.prompts import MAPPING_SCHEMA, MAPPING_SYSTEM, MAPPING_TASK_KEY
from app.review import service as review_service
from app.review.models import ProposalKind

logger = logging.getLogger(__name__)
CHECKPOINT_KEY = "_framework_mapping"


def fingerprint(
    framework_id: int, prompt: str, run_key: str | None, *, model: str | None = None,
) -> str:
    encoded = json.dumps(
        ["v1", run_key, model, framework_id, MAPPING_SYSTEM, MAPPING_SCHEMA, prompt],
        sort_keys=True,
        ensure_ascii=False,
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
            LLMCall.task_key == MAPPING_TASK_KEY,
            LLMCall.status == "ok",
            LLMCall.redaction_hits.contains({CHECKPOINT_KEY: {"fingerprint": key}}),
        )
        .order_by(LLMCall.id.desc())
        .limit(1)
    )


async def run_mapping(
    session: AsyncSession, framework_id: int, *, run_key: str | None = None
) -> dict[str, Any]:
    framework = await session.get(Framework, framework_id)
    if framework is None:
        raise NotFound("框架不存在", code="framework.not_found")

    controls = list(await session.scalars(active_controls().order_by(Control.id)))
    if not controls:
        raise AppError("控制点库为空，无法映射；请先在确认队列中确认控制点", code="mapping.no_controls")
    by_id = {control.id: control for control in controls}

    items = list(await session.scalars(
        select(FrameworkItem)
        .where(FrameworkItem.framework_id == framework_id)
        .order_by(FrameworkItem.order_index, FrameworkItem.id)
    ))
    raw_batches = build_batches(items)

    # K16/K17：每个框架项预筛 TOP_K 个候选控制点，提示词只放候选。
    # 控制点不多于 TOP_K 时不必预筛——全给，与原先行为一致。
    mappable = [
        (item.id, item_text(item))
        for batch in raw_batches for item in batch.items if item.id in batch.mappable_ids
    ]
    vector_used = False
    candidate_source = "all"
    fallback_reason: str | None = None
    if len(controls) <= TOP_K:
        candidates = {item_id: list(by_id) for item_id, _ in mappable}
    else:
        control_docs = [(control.id, control_text(control)) for control in controls]
        # 默认关闭；打开后失败一律回退（见 jev_prefilter 模块说明与 K17）。
        jev_candidates, fallback_reason = await jev_prefilter.rank(
            session, mappable, control_docs, top_k=TOP_K,
        )
        if jev_candidates is not None:
            candidates, candidate_source = jev_candidates, "jev"
        else:
            item_vectors, control_vectors = await load_vectors(session, mappable, list(by_id))
            vector_used = bool(item_vectors and control_vectors)
            candidates = rank_candidates(
                mappable, control_docs,
                item_vectors=item_vectors,
                control_vectors=control_vectors,
            )
            candidate_source = "keyword_vector"
        # 预筛的调用与回退记录与后续批次一起提交；批次各自 commit，先落地这一段。
        await session.commit()

    def _size(control_ids: list[int]) -> int:
        return len(render_controls_unchecked([by_id[cid] for cid in control_ids]))

    batches = []
    # 控制点不多于 TOP_K 时没有预筛，保持原来的分批（全库上下文，与旧行为一致）。
    sized = raw_batches if len(controls) <= TOP_K else [
        piece for batch in raw_batches for piece in chunk_items(batch)
    ]
    for batch in sized:
        for piece, control_ids in split_to_fit(batch, candidates, _size):
            if not control_ids:
                continue
            batches.append((
                render_items(piece),
                piece.mappable_ids,
                render_controls([by_id[cid] for cid in control_ids]),
            ))
    summary: dict[str, Any] = {
        "batches": len(batches),
        "candidate_vector_used": vector_used,
        "candidate_source": candidate_source,
        "candidate_fallback_reason": fallback_reason if fallback_reason != "disabled" else None,
        "proposals": 0,
        "rejected": 0,
        "failed": 0,
        "dropped_mappings": 0,
        "completed_batches": 0,
        "skipped_batches": 0,
        "attempted_batches": 0,
        "proposal_ids": [],
        "llm_call_ids": [],
        "resumed_proposal_ids": [],
    }

    for items_text, item_ids, controls_text in batches:
        prompt = (
            "# Framework items to assess\n"
            + items_text
            + "\n# Candidate controls (pre-selected as most relevant to these items)\n"
            + controls_text
            + "\nOutput JSON Schema:\n"
            + json.dumps(MAPPING_SCHEMA)
        )
        key = fingerprint(
            framework_id, prompt, run_key, model=await routed_model(session, MAPPING_TASK_KEY),
        )
        cached = await _checkpoint(session, key)
        resumed = (
            cached.redaction_hits[CHECKPOINT_KEY]["proposal_ids"]
            if cached is not None
            else None
        )
        # 检查点查完立刻提交：advisory 锁是事务级的，不提交就会被攥到批次结束，
        # 也就跨越了整个 LLM 调用。实测一次挂起的调用把事务开了 100 分钟，
        # pg_stat_activity 显示 idle in transaction / ClientRead，锁一并卡住。
        # 代价：重复投递不再被串行化，两次投递可能都跑一遍模型；检查点仍能防止
        # 跨轮重复做功，且提案创建本身是幂等落库的。
        await session.commit()
        if resumed is not None:
            summary["skipped_batches"] += 1
            summary["resumed_proposal_ids"].extend(resumed)
            continue

        validator = MappingCitationValidator(session, item_ids=item_ids)
        summary["attempted_batches"] += 1
        try:
            # 引用校验不进 run()：runner 只对 schema 失败做纠错重试，引用失败
            # 是直接抛出的，所以移到这里逐条判不损失修复机会，却能把「一条不合格
            # 整批作废」变成「只丢那一条」。schema 校验与其重试仍在 run() 内。
            result = await run(
                session,
                task_key=MAPPING_TASK_KEY,
                system=MAPPING_SYSTEM,
                prompt=prompt,
                schema=MAPPING_SCHEMA,
            )
        except ValidationFailure as failure:
            logger.warning("Framework %s mapping rejected: %s", framework_id, failure.reason)
            summary["rejected"] += 1
            await session.commit()
            continue
        except ProviderError as failure:
            # 供应商故障不是质量信号，不计入 rejected，也不该让整轮清零：
            # 实测一次 ReadTimeout 让 51 分钟、20 个已完成批次的工作全部作废。
            logger.warning("Framework %s mapping batch failed: %s", framework_id, failure)
            summary["failed"] += 1
            await session.commit()
            continue
        except Exception:
            await session.commit()
            raise

        call = await session.get(LLMCall, result.llm_call_id)
        if call is None:
            raise RuntimeError("Mapping runner did not persist its LLMCall")

        entries = result.payload["mappings"]
        # 快路径：整批一次校验；只有出问题时才逐条找出是哪几条。
        if entries and await validator.check(result.payload) is not None:
            kept = []
            for entry in entries:
                reason = await validator.check({"mappings": [entry]})
                if reason is None:
                    kept.append(entry)
                else:
                    logger.warning(
                        "Framework %s dropped one mapping: %s", framework_id, reason
                    )
                    summary["dropped_mappings"] += 1
            entries = kept

        ids: list[int] = []
        try:
            async with session.begin_nested():
                for entry in entries:
                    proposal = await review_service.create(
                        session,
                        kind=ProposalKind.MAPPING,
                        payload=entry,
                        citations=[{
                            "framework_item_id": entry["framework_item_id"],
                            "quote": entry["framework_item_quote"],
                        }],
                        confidence=entry["confidence"],
                        llm_call_id=result.llm_call_id,
                        document_id=None,
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


async def map_framework(ctx: dict[str, Any], framework_id: int) -> dict[str, Any]:
    async with session_factory() as session:
    # 检查点身份 = 输入 + 模型。原来入口把每次投递的 job_id 当 run_key 传进来，
    # 用户每点一次，同样的批次就重新付费调一次模型、再出一整套重复提案。
    # run_key 只留给评估脚本：它们要的正是「同样输入再跑一遍」。
        return await run_mapping(session, framework_id)
