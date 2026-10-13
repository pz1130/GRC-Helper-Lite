"""批量向量化。

本模块不写任何网络代码：脱敏、留痕、预算检查、失败留痕都由 M1 的
llm.embed() 完成。这里只负责分批、写回、断点续跑。
"""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import ClauseChunk
from app.llm.routing import resolve
from app.llm.runner import EMBEDDING_TASK_KEY, embed

BATCH_SIZE = 64


async def current_model(session: AsyncSession) -> str:
    config, _ = await resolve(session, EMBEDDING_TASK_KEY)
    return config.model


async def embed_pending(session: AsyncSession, *, limit: int = 500) -> dict[str, object]:
    model = await current_model(session)
    chunks = list(
        await session.scalars(
            select(ClauseChunk)
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

    embedded = 0
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start : start + BATCH_SIZE]
        vectors, _ = await embed(session, texts=[chunk.text for chunk in batch])
        for chunk, vector in zip(batch, vectors, strict=True):
            chunk.embedding = vector
            chunk.embedding_model = model
            chunk.embedding_version = "v1"
        await session.flush()
        # 逐批落盘：上游中途失败时，已完成的批次不丢。
        await session.commit()
        embedded += len(batch)

    remaining = await session.scalar(
        select(func.count())
        .select_from(ClauseChunk)
        .where(
            or_(
                ClauseChunk.embedding.is_(None),
                ClauseChunk.embedding_model.is_distinct_from(model),
            )
        )
    )
    return {"embedded": embedded, "remaining": int(remaining or 0), "model": model}
