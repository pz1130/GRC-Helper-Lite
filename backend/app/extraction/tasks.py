"""经确认队列产出提案；每批跑完各自原子提交。

现成的 `LLMCall.redaction_hits` JSON 里带一个加了命名空间的检查点，与脱敏计数
并存，**不往那里写任何原文**。检查点记的是这一轮、文档、渲染后的批次，以及
提示词/schema 版本——空结果的成功批次也记。同一批次的重复投递由一把事务级
咨询锁串起来。核心调用方必须用独立的 session：这个函数会自己 commit。"""

import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.db import session_factory
from app.errors import Conflict, NotFound
from app.extraction.batching import MAX_BATCH_CHARS, build_batches, render
from app.extraction.citations import ClauseCitationValidator
from app.extraction.inventory import (
    INVENTORY_SCHEMA,
    INVENTORY_SYSTEM,
    NORMALIZE_SCHEMA,
    NORMALIZE_SYSTEM,
)
from app.extraction.inventory import (
    run_pipeline as run,
)
from app.extraction.prompts import EXTRACT_SCHEMA, EXTRACT_SYSTEM, EXTRACT_TASK_KEY
from app.extraction.semantic_review import REVIEW_SCHEMA, REVIEW_SYSTEM
from app.ingest.models import DocStatus, Document
from app.llm.budget import BudgetExceeded
from app.llm.models import LLMCall
from app.llm.providers.base import ProviderError
from app.llm.routing import routed_model
from app.llm.validation import ValidationFailure
from app.review import service as review_service
from app.review.models import ProposalKind

logger = logging.getLogger(__name__)
CHECKPOINT_KEY = "_control_extraction"


def fingerprint(
    document_id: int, prompt: str, run_key: str | None, *, model: str | None = None,
) -> str:
    encoded = json.dumps(
        ["v3", run_key, model, document_id, EXTRACT_SYSTEM, EXTRACT_SCHEMA,
         INVENTORY_SYSTEM, INVENTORY_SCHEMA, NORMALIZE_SYSTEM, NORMALIZE_SCHEMA, REVIEW_SYSTEM, REVIEW_SCHEMA, prompt],
        sort_keys=True, ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _checkpoint(session: AsyncSession, key: str) -> LLMCall | None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(bytes.fromhex(key)[:8], "big", signed=True)},
    )
    return await session.scalar(select(LLMCall).where(
        LLMCall.task_key == EXTRACT_TASK_KEY,
        LLMCall.status == "ok",
        LLMCall.redaction_hits.contains({CHECKPOINT_KEY: {"fingerprint": key}}),
    ).order_by(LLMCall.id.desc()).limit(1))


async def _last_call_id(session: AsyncSession) -> int | None:
    """runner 刚刚 flush 的那次失败调用。占位行指回它，轨迹才追得完整。

    取最后一条 error 的 control_extract 调用——runner 在抛 ValidationFailure
    之前一定已经把它写进去了（见 llm/runner.py）。
    """
    return await session.scalar(
        select(LLMCall.id)
        .where(LLMCall.task_key == EXTRACT_TASK_KEY, LLMCall.status == "error")
        .order_by(LLMCall.id.desc())
        .limit(1)
    )


async def run_extraction(
    session: AsyncSession, document_id: int, *, run_key: str | None = None,
) -> dict[str, Any]:
    document = await session.get(Document, document_id)
    if document is None:
        raise NotFound("文档不存在", code="document.not_found")
    if document.status != DocStatus.ACTIVE:
        raise Conflict("只有 active 文档可以抽取控制点", code="document.not_active_for_extraction")
    clauses = list(await session.scalars(
        select(Clause).where(Clause.document_id == document_id, Clause.status != "merged")
        .order_by(Clause.order_index, Clause.id)
    ))
    # 先一次性渲染完：逐批 commit 会让后面几批的 ORM 对象过期。
    batches = [
        (render(b), b.source_ids, [c.id for c in b.clauses]) for b in build_batches(clauses)
    ]
    summary: dict[str, Any] = {
        "batches": len(batches), "proposals": 0, "rejected": 0,
        "completed_batches": 0, "skipped_batches": 0, "attempted_batches": 0,
        "proposal_ids": [], "llm_call_ids": [], "resumed_proposal_ids": [],
        "failed_proposal_ids": [],
        "resumed_llm_call_ids": [],
    }
    for prompt, clause_ids, target_ids in batches:
        if len(prompt) > MAX_BATCH_CHARS:
            # 不截断限定条件，也不让异常输入消耗模型预算；失败记录在审核界面可见。
            # 理由原样显示在审核队列里（与模型校验理由同一栏），所以写英文句子。
            failed = await review_service.record_failed_batch(
                session, kind=ProposalKind.CONTROL_EXTRACT,
                reason=f"Clause text with required context is {len(prompt)} characters, "
                       f"over the {MAX_BATCH_CHARS}-character limit. Check the clause tree "
                       "and split the oversized clause before re-running extraction.",
                document_id=document_id,
                batch_fingerprint=fingerprint(document_id, prompt, run_key),
                clause_ids=target_ids,
            )
            summary["failed_proposal_ids"].append(failed.id)
            summary["rejected"] += 1
            await session.commit()
            continue
        input_model = await routed_model(session, EXTRACT_TASK_KEY)
        key = fingerprint(
            document_id, prompt, run_key, model=input_model,
        )
        cached = await _checkpoint(session, key)
        resumed = cached.redaction_hits[CHECKPOINT_KEY]["proposal_ids"] if cached else None
        # 检查点查完立刻提交：advisory 锁是事务级的，不提交就会被攥到批次结束，
        # 也就跨越了整个 LLM 调用。M5 标定实测：一次挂起的调用把事务开了 100
        # 分钟，pg_stat_activity 显示 idle in transaction / ClientRead。
        # 代价：重复投递不再被串行化；检查点仍防止跨轮重复做功。
        if cached is not None:
            summary["resumed_llm_call_ids"].append(cached.id)
        await session.commit()
        if resumed is not None:
            summary["skipped_batches"] += 1
            summary["resumed_proposal_ids"].extend(resumed)
            continue
        validator = ClauseCitationValidator(
            session, document_id=document_id, clause_ids=clause_ids,
            target_clause_ids=target_ids,
        )
        summary["attempted_batches"] += 1
        try:
            result = await run(
                session, task_key=EXTRACT_TASK_KEY, system=EXTRACT_SYSTEM,
                prompt=prompt, document_id=document_id, batch_fingerprint=key, target_ids=target_ids,
                schema=EXTRACT_SCHEMA, citation_validator=validator,
            )
        except ValidationFailure as failure:
            logger.warning("Document %s extraction rejected: %s", document_id, failure.reason)
            summary["rejected"] += 1
            # 留一行占位提案。不留的话这一批就静默消失了：错误只在 llm_call.error
            # 里，worker 日志不打，界面只说"任务已入队"，而审计员会以为这份文档
            # 就只抽出了这些控制点（生产栈实测 7 批丢 2 批）。
            #
            # 它的 status 是 failed 而不是 pending——没有东西可供人决策，
            # 不该进待确认角标；但它必须在队列里看得见，并且能一键重跑。
            failed = await review_service.record_failed_batch(
                session,
                kind=ProposalKind.CONTROL_EXTRACT,
                reason=failure.reason,
                document_id=document_id,
                llm_call_id=await _last_call_id(session),
                batch_fingerprint=key,
                clause_ids=target_ids,
            )
            summary["failed_proposal_ids"].append(failed.id)
            # runner 已经把出错的 LLMCall 刷进去了。留住它，但别把失败缓存下来。
            await session.commit()
            continue
        except BudgetExceeded as exhausted:
            # 预算耗尽是"任务被有意暂停"，不是故障。原先它是裸 Exception，
            # 走下面那条 re-raise：任务死掉、队列里一行都没有、也没有查状态的接口，
            # 用户只看到过一句"已排队"。留一行可见的记录，和失败批次同一形态。
            failed = await review_service.record_failed_batch(
                session,
                kind=ProposalKind.CONTROL_EXTRACT,
                reason=str(exhausted),
                document_id=document_id,
                batch_fingerprint=key,
                clause_ids=target_ids,
            )
            summary["failed_proposal_ids"].append(failed.id)
            summary["paused_reason"] = exhausted.code
            await session.commit()
            break
        except ProviderError:
            # provider 重试用完仍失败（断连、持续 5xx）。原来走下面那条裸 re-raise：
            # worker 的任务级重试也用完之后，任务就此结束，队列里一行都没有，剩下的
            # 批次也没抽——与预算耗尽修之前是同一个样子（K16）。
            #
            # 先留一行可见的失败记录，再照旧抛出：任务级重试靠这个异常续跑，某次
            # 重跑成功时 supersede_failed_batch 按指纹把这行撤下；全部失败时这行留在
            # 队列里，「重跑」会从检查点接着把剩下的批次也跑完。
            if not await review_service.has_failed_batch(session, key):
                failed = await review_service.record_failed_batch(
                    session,
                    kind=ProposalKind.CONTROL_EXTRACT,
                    reason="The AI provider kept failing after repeated attempts, so this batch "
                           "and the batches after it were not extracted. Retry to continue "
                           "from here; completed batches are not re-run.",
                    document_id=document_id,
                    llm_call_id=await _last_call_id(session),
                    batch_fingerprint=key,
                    clause_ids=target_ids,
                )
                summary["failed_proposal_ids"].append(failed.id)
            await session.commit()
            raise
        except Exception:
            # 留住 runner 的失败留痕；这时提案还没开始创建。
            await session.commit()
            raise
        call = await session.get(LLMCall, result.llm_call_id)
        if call is None:
            raise RuntimeError("Extraction runner did not persist its LLMCall")
        ids: list[int] = []
        try:
            async with session.begin_nested():
                for item in result.payload["controls"]:
                    proposal = await review_service.create(
                        session, kind=ProposalKind.CONTROL_EXTRACT, payload=item,
                        citations=item["citations"], confidence=item["confidence"],
                        llm_call_id=result.llm_call_id, document_id=document_id,
                    )
                    await session.flush()
                    ids.append(proposal.id)
                cited_ids = {
                    citation["clause_id"] for item in result.payload["controls"]
                    for citation in item["citations"]
                }
                call.redaction_hits = {
                    **(call.redaction_hits or {}),
                    CHECKPOINT_KEY: {
                        "fingerprint": key, "proposal_ids": ids,
                        "inventory_id": result.payload.get("inventory_id"),
                        "requirement_mappings": result.payload.get("requirement_mappings", []),
                        "document_id": document_id,
                        "input_model": input_model,
                        "target_clause_ids": target_ids,
                        "context_clause_ids": [cid for cid in clause_ids if cid not in target_ids],
                        "cited_target_clause_ids": [cid for cid in target_ids if cid in cited_ids],
                        "uncited_target_clause_ids": [cid for cid in target_ids if cid not in cited_ids],
                    },
                }
                await session.flush()
        except Exception:
            # 保存点把失败那批的提案全部撤掉，LLM 留痕保住。
            await session.commit()
            raise
        # 同一批次重跑成功了，让旧的失败行退出队列——**不删行**（AuditLog 按 id
        # 记实体），改成 rejected 并写明原因。留着是审计要求，退出是为了不让
        # 一条已经修好的失败继续占用人的注意力。
        await review_service.supersede_failed_batch(session, key)
        await session.commit()
        summary["completed_batches"] += 1
        summary["proposals"] += len(ids)
        summary["proposal_ids"].extend(ids)
        summary["llm_call_ids"].extend(result.payload.get("new_llm_call_ids", [result.llm_call_id]))
    return summary


async def extract_controls(ctx: dict[str, Any], document_id: int) -> dict[str, Any]:
    async with session_factory() as session:
    # 检查点身份 = 输入 + 模型。原来入口把每次投递的 job_id 当 run_key 传进来，
    # 用户每点一次，同样的批次就重新付费调一次模型、再出一整套重复提案。
    # run_key 只留给评估脚本：它们要的正是「同样输入再跑一遍」。
        return await run_extraction(session, document_id)
