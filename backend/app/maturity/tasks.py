"""跑成熟度建议：取输入 → 问模型 → 把分写进 maturity_scores。

**AI 的分不覆盖人工的分。** 成熟度分会进审计准备包，一个人工定过的分被 AI
悄悄改掉，报告里就出现了没人负责的数字。所以已有 human 来源的行直接跳过，
计入 skipped_human 如实报出来，而不是静默不动。

写的是正式表而不是 Proposal：`maturity_scores` 不在 CLAUDE.md 那四张
必须走 review/materialize 的表里，而且这张表从建表起就带着
`doc_score_source`/`impl_score_source` 两列——设计本来就是"AI 直接落分、
标明来源、人工可随时改写"。人工一改，来源翻成 human，此后 AI 不再碰它。
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_factory
from app.errors import AppError, Conflict, NotFound
from app.frameworks.models import FrameworkItem
from app.iam.audit import record
from app.iam.models import User
from app.llm.providers.base import ProviderError
from app.llm.runner import run
from app.llm.validation import ValidationFailure
from app.maturity.context import build_inputs
from app.maturity.models import (
    AssessmentStatus,
    MaturityAssessment,
    MaturityScore,
    ScoreSource,
)
from app.maturity.prompts import (
    MATURITY_SCHEMA,
    MATURITY_SYSTEM,
    MATURITY_TASK_KEY,
    MAX_SUGGESTIONS,
    render_items,
)

logger = logging.getLogger(__name__)


async def suggest_scores(
    session: AsyncSession, *, assessment_id: int, actor: User | None = None
) -> dict[str, Any]:
    assessment = await session.get(MaturityAssessment, assessment_id)
    if assessment is None:
        raise NotFound("成熟度评估不存在", code="maturity.assessment_not_found")
    if assessment.status is AssessmentStatus.FINAL:
        raise Conflict("已定稿的成熟度评估不能修改", code="maturity.assessment_finalized")

    inputs = await build_inputs(session, framework_id=assessment.framework_id)

    # 只在末级项上打分——和人工那条路同一条规矩（router.upsert_score）。
    parent_ids = set(
        await session.scalars(
            select(FrameworkItem.parent_id).where(
                FrameworkItem.framework_id == assessment.framework_id,
                FrameworkItem.parent_id.is_not(None),
            )
        )
    )
    leaves = [item for item in inputs.items if item.framework_item_id not in parent_ids]

    existing = {
        row.framework_item_id: row
        for row in await session.scalars(
            select(MaturityScore).where(MaturityScore.assessment_id == assessment_id)
        )
    }

    summary: dict[str, Any] = {
        "batches": 0,
        "written": 0,
        "rejected": 0,
        "failed": 0,
        "stopped_by": None,
        "skipped_human": 0,
        "unknown_items": 0,
        "stopped_finalized": False,
        "evidence_available": inputs.evidence_available,
        "llm_call_ids": [],
    }

    for start in range(0, len(leaves), MAX_SUGGESTIONS):
        batch = leaves[start : start + MAX_SUGGESTIONS]
        # 只认这一批的 id。放宽到「整个框架的末级项」等于允许模型拿这一批的上下文
        # 去改上一批的分——落错行，而且 unknown_items 还是 0，没人看得见。
        # 控制点原文是逐字渲进提示词的，所以这也是注入面的边界。
        allowed = {item.framework_item_id for item in batch}
        from dataclasses import replace

        prompt = (
            render_items(replace(inputs, items=batch))
            + "\nOutput JSON Schema:\n"
            + json.dumps(MATURITY_SCHEMA)
        )
        summary["batches"] += 1
        # 与映射任务同一套分类（K7）。这一批失败，前面已提交的批次仍然有效；
        # 失败那次调用的 llm_call 由 run() 记进本会话，提交了花费才不会少计。
        try:
            result = await run(
                session,
                task_key=MATURITY_TASK_KEY,
                system=MATURITY_SYSTEM,
                prompt=prompt,
                schema=MATURITY_SCHEMA,
            )
        except ValidationFailure as failure:
            logger.warning("Maturity assessment %s batch rejected: %s", assessment_id, failure.reason)
            summary["rejected"] += 1
            await session.commit()
            continue
        except ProviderError as failure:
            # 供应商故障不是质量信号，不计入 rejected，也不该让一次超时作废整轮。
            logger.warning("Maturity assessment %s batch failed: %s", assessment_id, failure)
            summary["failed"] += 1
            await session.commit()
            continue
        except AppError as stop:
            # 预算耗尽之类：后面的批次也不会成功，停下。照「中途定稿」的做法正常返回，
            # 让任务入口照样写汇总审计——前端不轮询任务状态，这条审计是这一轮停在
            # 哪、为什么停的唯一记录。
            logger.warning("Maturity assessment %s stopped: %s", assessment_id, stop.code)
            await session.rollback()
            summary["stopped_by"] = stop.code
            break
        if result.llm_call_id is not None:
            summary["llm_call_ids"].append(result.llm_call_id)

        # 一轮要几分钟。原来整轮一个事务、开头拍一次快照：跑的期间有人定了稿，分照样
        # 写进已定稿的评估；有人手动改了某项，AI 按旧快照当它是 AI 分，把人工分覆盖掉。
        # 现在每批落分前锁住评估与这一批的行、重读，逐批提交。
        locked = await session.scalar(
            select(MaturityAssessment)
            .where(MaturityAssessment.id == assessment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None or locked.status is AssessmentStatus.FINAL:
            summary["stopped_finalized"] = True
            break
        existing.update({
            row.framework_item_id: row
            for row in await session.scalars(
                select(MaturityScore)
                .where(
                    MaturityScore.assessment_id == assessment_id,
                    MaturityScore.framework_item_id.in_(allowed),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        })

        for entry in result.payload["suggestions"]:
            item_id = entry["framework_item_id"]
            if item_id not in allowed:
                # 模型回了一个不在这批里的 id——丢掉，不要凭空建行
                summary["unknown_items"] += 1
                continue
            row = existing.get(item_id)
            if row is not None and ScoreSource.HUMAN in (
                row.doc_score_source,
                row.impl_score_source,
            ):
                summary["skipped_human"] += 1
                continue
            if row is None:
                row = MaturityScore(
                    assessment_id=assessment_id,
                    framework_item_id=item_id,
                    doc_score=entry["doc_score"],
                    impl_score=entry["impl_score"],
                    doc_rationale=entry["doc_rationale"],
                    impl_rationale=entry["impl_rationale"],
                    doc_score_source=ScoreSource.AI,
                    impl_score_source=ScoreSource.AI,
                )
                session.add(row)
                existing[item_id] = row
            else:
                # 就地改写会把上一轮的分和理由冲掉，而这些分要进审计准备包——
                # 所以先把旧值留进审计（铁律二），再写新值。
                before = {
                    "doc_score": row.doc_score,
                    "impl_score": row.impl_score,
                    "doc_rationale": row.doc_rationale,
                    "impl_rationale": row.impl_rationale,
                    "scored_at": row.scored_at.isoformat() if row.scored_at else None,
                }
                row.doc_score = entry["doc_score"]
                row.impl_score = entry["impl_score"]
                row.doc_rationale = entry["doc_rationale"]
                row.impl_rationale = entry["impl_rationale"]
                row.doc_score_source = row.impl_score_source = ScoreSource.AI
                # scored_at 只有 server_default、没有 onupdate，不显式写的话
                # 改写后仍报第一次的时间。
                row.scored_at = datetime.now(UTC)
                await record(
                    session,
                    user=actor,
                    action="maturity_score.ai_overwrite",
                    entity_type="MaturityScore",
                    entity_id=row.id,
                    before=before,
                    after={
                        "doc_score": row.doc_score,
                        "impl_score": row.impl_score,
                        "doc_rationale": row.doc_rationale,
                        "impl_rationale": row.impl_rationale,
                        "scored_at": row.scored_at.isoformat(),
                    },
                )
            summary["written"] += 1
        await session.commit()

    await session.flush()
    return summary


async def suggest_maturity_scores(
    ctx: dict[str, Any], assessment_id: int, actor_id: int
) -> dict[str, Any]:
    """队列入口：ARQ 调的是这个，HTTP 请求只负责投递。

    为什么不能留在请求里：一轮 suggest 是 ceil(末级项 / MAX_SUGGESTIONS) 次串行
    provider 调用，800-53 的 1034 条就是 52 次。worker.py 写着这类任务「全都远超
    15 分钟」——抽取、映射、冲突检测因此都是 LONG_JOB_TIMEOUT 的队列任务。留在
    请求里的后果不是慢，是网关超时后 commit 永远到不了，已经跑完的批次全部回滚，
    而那些 token 已经花掉且计了费。
    """
    async with session_factory() as session:
        actor = await session.get(User, actor_id)
        if actor is None:
            return {"written": 0, "failed": 1}
        try:
            summary = await suggest_scores(
                session, assessment_id=assessment_id, actor=actor
            )
        except AppError:
            await session.rollback()
            raise
        await record(
            session,
            user=actor,
            action="maturity_score.suggest",
            entity_type="MaturityAssessment",
            entity_id=assessment_id,
            before=None,
            after=summary,
        )
        await session.commit()
        return summary
