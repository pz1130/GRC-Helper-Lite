from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import ClauseChunk
from app.db import get_session
from app.errors import NotFound
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.indexing.embedder import current_model
from app.ingest.models import Document
from app.worker import enqueue

router = APIRouter(prefix="/api/index", tags=["index"])


@router.post("/documents/{document_id}")
async def index_one(
    document_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: User = Depends(require(Permission.DOCUMENT_WRITE)),
) -> dict[str, str]:
    if await session.get(Document, document_id) is None:
        raise NotFound("文档不存在", code="document.not_found")
    return {"job_id": await enqueue("index_document", document_id)}


@router.post("/rebuild")
async def rebuild(
    _: User = Depends(require(Permission.DOCUMENT_WRITE)),
) -> dict[str, str]:
    return {"job_id": await enqueue("reindex_all")}


@router.get("/status")
async def status(
    _: User = Depends(require(Permission.READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    try:
        model = await current_model(session)
    except Exception:  # noqa: BLE001 — 没配 provider 时状态页仍要能打开
        model = ""

    total = await session.scalar(select(func.count()).select_from(ClauseChunk)) or 0
    pending = (
        await session.scalar(
            select(func.count())
            .select_from(ClauseChunk)
            .where(
                or_(
                    ClauseChunk.embedding.is_(None),
                    ClauseChunk.embedding_model.is_distinct_from(model),
                )
            )
        )
        or 0
    )
    return {
        "model": model,
        "total": int(total),
        "embedded": int(total) - int(pending),
        "pending": int(pending),
    }
