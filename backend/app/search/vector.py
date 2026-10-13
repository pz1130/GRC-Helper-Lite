"""pgvector 近邻检索。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import ClauseChunk
from app.ingest.models import superseded_document_ids
from app.search.fulltext import RankedChunk


async def search(
    session: AsyncSession,
    embedding: list[float],
    *,
    limit: int = 50,
    document_id: int | None = None,
    model: str | None = None,
) -> list[RankedChunk]:
    distance = ClauseChunk.embedding.cosine_distance(embedding)
    stmt = (
        select(ClauseChunk.id, distance.label("distance"))
        .where(ClauseChunk.embedding.is_not(None))
        .order_by(distance, ClauseChunk.id)
        .limit(limit)
    )
    if document_id is not None:
        stmt = stmt.where(ClauseChunk.document_id == document_id)
    else:
        # 不指定文档时只搜现行制度；明确在旧版里搜是查历史，上面照常放行。
        stmt = stmt.where(ClauseChunk.document_id.notin_(superseded_document_ids()))
    if model is not None:
        stmt = stmt.where(ClauseChunk.embedding_model == model)

    rows = (await session.execute(stmt)).all()
    return [
        RankedChunk(chunk_id=row.id, rank=position, score=1.0 - float(row.distance))
        for position, row in enumerate(rows, start=1)
    ]
