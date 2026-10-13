from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.edit import merge_clauses, split_clause
from app.clauses.models import Clause
from app.clauses.schemas import ClauseMergeIn, ClauseOut, ClauseSplitIn
from app.db import get_session
from app.errors import NotFound
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.ingest.models import Document
from app.worker import enqueue

router = APIRouter(prefix="/api/clauses", tags=["clauses"])


@router.get("/{clause_id}/source-blocks")
async def get_sources(
    clause_id: int,
    _: Annotated[User, Depends(require(Permission.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict:
    from app.clauses.sources import source_page

    clause = await session.get(Clause, clause_id)
    if clause is None:
        raise NotFound("条款不存在", code="clause.not_found")
    return await source_page(session, clause.document_id, clause_id=clause_id,
                             offset=offset, limit=limit)


def _out(clause: Clause, document_title: str) -> ClauseOut:
    return ClauseOut(
        id=clause.id,
        document_id=clause.document_id,
        document_title=document_title,
        number=clause.number,
        heading=clause.heading,
        heading_path=clause.heading_path,
        citation_label=clause.citation_label,
        text=clause.text,
        level=clause.level,
        page_ref=clause.page_ref,
    )


@router.get("/{clause_id}", response_model=ClauseOut)
async def get_clause(
    *,
    clause_id: int,
    _: Annotated[User, Depends(require(Permission.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClauseOut:
    row = (
        await session.execute(
            select(Clause, Document.title)
            .join(Document, Document.id == Clause.document_id)
            .where(Clause.id == clause_id)
        )
    ).first()
    if row is None:
        raise NotFound("条款不存在", code="clause.not_found")
    clause, document_title = row
    return _out(clause, document_title)


@router.post("/{clause_id}/split", response_model=ClauseOut)
async def split(
    *,
    clause_id: int,
    payload: ClauseSplitIn,
    actor: Annotated[User, Depends(require(Permission.DOCUMENT_WRITE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClauseOut:
    created = await split_clause(session, clause_id, at=payload.at, actor=actor)
    document = await session.get(Document, created.document_id)
    await session.commit()
    await enqueue("index_document", created.document_id)
    return _out(created, document.title if document else "")


@router.post("/{clause_id}/merge", response_model=ClauseOut)
async def merge(
    *,
    clause_id: int,
    payload: ClauseMergeIn,
    actor: Annotated[User, Depends(require(Permission.DOCUMENT_WRITE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClauseOut:
    winner = await merge_clauses(
        session, winner_id=payload.into_id, loser_id=clause_id, actor=actor
    )
    document = await session.get(Document, winner.document_id)
    await session.commit()
    await enqueue("index_document", winner.document_id)
    return _out(winner, document.title if document else "")
