"""生成审计答复的后台任务。"""

from typing import Any

from sqlalchemy import select

from app.audit.models import AuditEngagement, AuditQuestion, QuestionStatus
from app.audit.service import generate_answer
from app.db import session_factory
from app.errors import AppError
from app.iam.audit import record
from app.iam.models import User
from app.llm.providers.base import ProviderError
from app.llm.validation import ValidationFailure


async def generate_engagement_answers(
    ctx: dict[str, Any], engagement_id: int, actor_id: int
) -> dict[str, Any]:
    """给每一个还没有答复的审计问题各生成一条待审提案。"""
    async with session_factory() as session:
        actor = await session.get(User, actor_id)
        engagement = await session.get(AuditEngagement, engagement_id)
        if actor is None or engagement is None:
            return {"generated": 0, "skipped": 0, "failed": 1}
        questions = list(
            await session.scalars(
                select(AuditQuestion)
                .where(
                    AuditQuestion.engagement_id == engagement_id,
                    AuditQuestion.status == QuestionStatus.PENDING,
                )
                .order_by(AuditQuestion.seq)
            )
        )
        generated = skipped = failed = 0
        failures: list[dict[str, Any]] = []
        for question in questions:
            try:
                await generate_answer(session, question.id, question.language, actor=actor)
                await session.commit()
                generated += 1
            except (AppError, ProviderError, ValidationFailure) as exc:
                await session.rollback()
                failed += 1
                failures.append({"question_id": question.id, "reason": str(exc)[:500]})
        await record(
            session,
            user=actor,
            action="audit_answers.batch_complete",
            entity_type="AuditEngagement",
            entity_id=engagement_id,
            after={
                "job_id": ctx.get("job_id"),
                "generated": generated,
                "skipped": skipped,
                "failed": failed,
                "failures": failures[:20],
            },
        )
        await session.commit()
        return {
            "generated": generated,
            "skipped": skipped,
            "failed": failed,
            "failures": failures,
        }
