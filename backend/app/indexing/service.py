"""落库条款 chunk，并找出还需要 embed 的那些。"""

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause, ClauseChunk
from app.indexing.chunking import Chunk, split, with_context


async def rebuild_chunks(session: AsyncSession, *, document_id: int) -> int:
    """按条款重建该文档的全部 chunk，并清空旧向量。"""
    await session.execute(delete(ClauseChunk).where(ClauseChunk.document_id == document_id))

    clauses = list(
        await session.scalars(
            select(Clause)
            .where(Clause.document_id == document_id, Clause.status != "merged")
            .order_by(Clause.order_index)
        )
    )

    total = 0
    for clause in clauses:
        body = with_context(clause.heading_path, clause.text)
        pieces = split(body) or [Chunk(text=body, index=0)]
        for piece in pieces:
            session.add(
                ClauseChunk(
                    clause_id=clause.id,
                    document_id=document_id,
                    chunk_index=total,
                    text=piece.text,
                    language=clause.language,
                )
            )
            total += 1
    await session.flush()
    return total


async def pending_chunk_ids(
    session: AsyncSession, *, model: str, limit: int = 500
) -> list[int]:
    """返回尚未按当前 embedding 模型向量化的 chunk。"""
    return list(
        await session.scalars(
            select(ClauseChunk.id)
            .where(
                or_(
                    ClauseChunk.embedding.is_(None),
                    ClauseChunk.embedding_model.is_distinct_from(model),
                )
            )
            .order_by(ClauseChunk.id)
            .limit(limit)
        )
    )
