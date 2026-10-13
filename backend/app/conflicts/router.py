from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.conflicts.models import PolicyConflict
from app.conflicts.schemas import ConflictOut, ConflictSideOut
from app.db import get_session
from app.errors import Conflict
from app.iam.audit import record
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.ingest.models import Document
from app.worker import enqueue

router = APIRouter(prefix="/api/conflicts", tags=["conflicts"])

Reader = Annotated[User, Depends(require(Permission.READ))]
Writer = Annotated[User, Depends(require(Permission.CONTROL_WRITE))]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[ConflictOut])
async def list_conflicts(*, _: Reader, session: Session) -> list[ConflictOut]:
    rows = (
        await session.execute(
            select(PolicyConflict).order_by(PolicyConflict.id)
        )
    ).scalars().all()
    if not rows:
        return []

    wanted = {row.clause_a_id for row in rows} | {row.clause_b_id for row in rows}
    sides = {
        clause_id: ConflictSideOut(
            clause_id=clause_id,
            document_id=document_id,
            document_title=title,
            citation_label=label,
            text=text or "",
        )
        for clause_id, document_id, title, label, text in await session.execute(
            select(Clause.id, Clause.document_id, Document.title, Clause.citation_label, Clause.text)
            .join(Document, Document.id == Clause.document_id)
            .where(Clause.id.in_(wanted))
        )
    }
    return [
        ConflictOut(
            id=row.id,
            topic=row.topic,
            difference=row.difference,
            confidence=row.confidence,
            created_at=row.created_at,
            side_a=sides[row.clause_a_id],
            side_b=sides[row.clause_b_id],
        )
        for row in rows
    ]


@router.post("/detect", status_code=202)
async def trigger_detection(
    *,
    actor: Writer,
    session: Session,
    max_batches: int | None = Query(default=None, ge=1, le=1000),
) -> dict[str, str]:
    """排队冲突检测；验收时可用 max_batches 先做有界抽样。"""
    args = (max_batches,) if max_batches is not None else ()
    job_id = await enqueue("detect_conflicts", *args)
    if not job_id:
        raise Conflict("冲突检测任务未能进入队列", code="task.conflict_enqueue_failed")
    await record(
        session, user=actor, action="conflicts.enqueue", entity_type="PolicyConflict",
        entity_id=0, after={"job_id": job_id, "max_batches": max_batches},
    )
    await session.commit()
    return {"job_id": job_id}
