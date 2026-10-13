"""建检索索引、算 embedding 的 ARQ 任务。"""

import logging
from typing import Any

from sqlalchemy import select

from app.clauses.models import Clause, ClauseChunk
from app.db import session_factory
from app.indexing.embedder import embed_pending
from app.indexing.service import rebuild_chunks
from app.llm.routing import RoutingError

logger = logging.getLogger(__name__)


async def index_document(ctx: dict[str, Any], document_id: int) -> dict[str, Any]:
    async with session_factory() as session:
        chunks = await rebuild_chunks(session, document_id=document_id)
        await session.commit()
        try:
            result = await embed_pending(session)
        except RoutingError as exc:
            # 没配 embedding provider 是**预期状态**，不是故障：切块已经落库，
            # 检索会降级成纯关键词。当成失败重试三次只会刷屏，且掩盖真故障。
            logger.info("跳过向量化：%s", exc)
            return {"chunks": chunks, "embedded": 0, "remaining": chunks, "skipped": str(exc)}
        return {"chunks": chunks, **result}


async def reindex_all(ctx: dict[str, Any]) -> dict[str, Any]:
    """换 embedding 模型后的全量重建。"""
    async with session_factory() as session:
        # 早期的纯文本导入可能有条款，却一个检索块都没有。
        missing_documents = list(await session.scalars(
            select(Clause.document_id)
            .outerjoin(ClauseChunk, ClauseChunk.clause_id == Clause.id)
            .where(Clause.status != "merged", ClauseChunk.id.is_(None))
            .distinct()
        ))
        for document_id in missing_documents:
            await rebuild_chunks(session, document_id=document_id)
        await session.commit()
        total = 0
        while True:
            try:
                result = await embed_pending(session, limit=500)
            except RoutingError as exc:
                logger.info("跳过向量化：%s", exc)
                return {"embedded": total, "model": "", "skipped": str(exc)}
            total += int(result["embedded"])
            if not result["embedded"] or not result["remaining"]:
                return {"embedded": total, "model": result["model"]}
