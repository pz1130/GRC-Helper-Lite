"""冲突检测；骨架照抄 M6 硬化后的 relations/tasks.py。

继承而来的六条，均有出处，不重新踩：检查点查完立刻提交（advisory 锁不得
跨越 LLM 调用）、ProviderError 计入 failed 并继续、引用逐条丢弃而非整批
作废、按批原子提交、schema 带 maxItems、总时限与 <think> 剥离在 runner 内。

第七条是本期新加的：跑之前先补向量。OQ-12 记录了 146 个控制点里有 10 个
从未被 embed，第一级粗筛完全看不见它们。把补向量写成任务自身的前置检查，
而不是一次性的手工操作——否则每批新控制点入库后都会重演。
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.conflicts.citations import ConflictCitationValidator
from app.conflicts.clustering import clause_bundles, cross_document_pairs
from app.conflicts.models import normalise_pair
from app.conflicts.prompts import (
    CONFLICT_SCHEMA,
    CONFLICT_SYSTEM,
    CONFLICT_TASK_KEY,
    clause_chars,
    render_pairs,
)
from app.db import session_factory
from app.llm.models import LLMCall
from app.llm.providers.base import ProviderError
from app.llm.routing import routed_model
from app.llm.runner import run
from app.llm.validation import ValidationFailure
from app.relations.clustering import batch_pairs
from app.relations.indexing import embed_pending
from app.review import service as review_service
from app.review.models import ProposalKind

logger = logging.getLogger(__name__)
CHECKPOINT_KEY = "_conflict_detection"
MAX_BATCH_CHARS = 12_000


@dataclass(frozen=True)
class Batch:
    system: str
    prompt: str
    clause_ids: list[int]
    allowed_pairs: tuple[frozenset[int], ...]


def conflict_key(entry: dict[str, Any]) -> tuple[int, int, str]:
    """一条冲突的身份。与 materialize 用同一条规范化规则。"""
    low, high = normalise_pair(entry["clause_a_id"], entry["clause_b_id"])
    return low, high, entry["topic"].strip()


def fingerprint(system: str, prompt: str, run_key: str | None, *, model: str | None = None) -> str:
    digest = hashlib.sha256()
    for part in (system, prompt, run_key or "", model or ""):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


async def _checkpoint(session: AsyncSession, key: str) -> LLMCall | None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(bytes.fromhex(key)[:8], "big", signed=True)},
    )
    return await session.scalar(
        select(LLMCall)
        .where(
            LLMCall.task_key == CONFLICT_TASK_KEY,
            LLMCall.status == "ok",
            LLMCall.redaction_hits.contains({CHECKPOINT_KEY: {"fingerprint": key}}),
        )
        .order_by(LLMCall.id.desc())
        .limit(1)
    )


async def _build_batches(session: AsyncSession) -> list[Batch]:
    pairs = await cross_document_pairs(session)
    if not pairs:
        return []
    bundles = await clause_bundles(session, pairs)
    sizes = clause_chars(bundles)

    batches: list[Batch] = []
    for group in batch_pairs(pairs, sizes=sizes, max_chars=MAX_BATCH_CHARS):
        clause_ids: list[int] = []
        allowed: list[frozenset[int]] = []
        for pair in group:
            left = [ref.clause_id for ref in bundles.get(pair.low, [])]
            right = [ref.clause_id for ref in bundles.get(pair.high, [])]
            clause_ids.extend(left + right)
            # 闸 3 逐对核：一批并排 25 对，只查「两端都在本批次」挡不住模型
            # 把第 1 对的左边和第 7 对的右边连起来。
            for a in left:
                for b in right:
                    allowed.append(frozenset((a, b)))
        batches.append(
            Batch(
                system=CONFLICT_SYSTEM,
                prompt=render_pairs(group, bundles),
                clause_ids=sorted(set(clause_ids)),
                allowed_pairs=tuple(allowed),
            )
        )
    return batches


async def run_detection(
    session: AsyncSession, *, run_key: str | None = None, max_batches: int | None = None
) -> dict[str, Any]:
    # 先补向量再取候选：缺向量的控制点在第一级里根本不会出现（OQ-12）。
    await embed_pending(session)
    await session.commit()

    batches = await _build_batches(session)
    total_batches = len(batches)
    if max_batches is not None:
        if max_batches <= 0:
            raise ValueError("max_batches 必须为正")
        batches = batches[:max_batches]
    summary: dict[str, Any] = {
        "batches": len(batches),
        "total_batches": total_batches,
        "limited": len(batches) < total_batches,
        "proposals": 0,
        "rejected": 0,
        "failed": 0,
        "dropped_conflicts": 0,
        "duplicate_conflicts": 0,
        "completed_batches": 0,
        "skipped_batches": 0,
        "attempted_batches": 0,
        "proposal_ids": [],
        "llm_call_ids": [],
        "resumed_proposal_ids": [],
    }

    # 同一对条款可能出现在两个批次里（一个控制点的多条条款分散在不同对中）。
    # 不去重就会产出两条一模一样的待确认提案。
    seen: set[tuple[int, int, str]] = set()

    for batch in batches:
        prompt = batch.prompt + "\nOutput JSON Schema:\n" + json.dumps(CONFLICT_SCHEMA)
        key = fingerprint(
            batch.system, prompt, run_key, model=await routed_model(session, CONFLICT_TASK_KEY),
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

        validator = ConflictCitationValidator(
            session, clause_ids=batch.clause_ids, allowed_pairs=batch.allowed_pairs
        )
        summary["attempted_batches"] += 1
        try:
            # 引用校验不进 run()：runner 只对 schema 失败做纠错重试，引用失败
            # 直接抛出，移到这里逐条判可把「一条不合格整批作废」变成「只丢那一条」。
            result = await run(
                session,
                task_key=CONFLICT_TASK_KEY,
                system=batch.system,
                prompt=prompt,
                schema=CONFLICT_SCHEMA,
            )
        except ValidationFailure as failure:
            logger.warning("Conflict batch rejected: %s", failure.reason)
            summary["rejected"] += 1
            await session.commit()
            continue
        except ProviderError as failure:
            logger.warning("Conflict batch failed: %s", failure)
            summary["failed"] += 1
            await session.commit()
            continue
        except Exception:
            await session.commit()
            raise

        call = await session.get(LLMCall, result.llm_call_id)
        if call is None:
            raise RuntimeError("Conflict runner did not persist its LLMCall")

        entries = result.payload["conflicts"]
        if entries and await validator.check(result.payload) is not None:
            kept = []
            for entry in entries:
                reason = await validator.check({"conflicts": [entry]})
                if reason is None:
                    kept.append(entry)
                else:
                    logger.warning("Dropped one conflict: %s", reason)
                    summary["dropped_conflicts"] += 1
            entries = kept

        unique = []
        for entry in entries:
            marker = conflict_key(entry)
            if marker in seen:
                logger.info("Skipped a conflict already proposed this run: %s", marker)
                summary["duplicate_conflicts"] += 1
                continue
            seen.add(marker)
            unique.append(entry)
        entries = unique

        ids: list[int] = []
        try:
            async with session.begin_nested():
                for entry in entries:
                    proposal = await review_service.create(
                        session,
                        kind=ProposalKind.CONFLICT,
                        payload=entry,
                        citations=[
                            {"clause_id": entry["clause_a_id"], "quote": entry["quote_a"]},
                            {"clause_id": entry["clause_b_id"], "quote": entry["quote_b"]},
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


async def detect_conflicts(
    ctx: dict[str, Any], max_batches: int | None = None
) -> dict[str, Any]:
    # 检查点身份 = 输入 + 模型，不含 job_id：原来每投递一次就把同样的批次重新付费跑一遍。
    async with session_factory() as session:
        return await run_detection(
            session, max_batches=max_batches
        )
