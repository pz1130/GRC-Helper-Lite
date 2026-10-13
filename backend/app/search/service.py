"""检索编排：扩展 → 全文 + 向量 → RRF 融合 → 回填。"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause, ClauseChunk
from app.indexing.embedder import current_model
from app.ingest.models import Document
from app.llm.routing import RoutingError
from app.llm.runner import embed
from app.search import fulltext, vector
from app.search.expansion import expand
from app.search.fusion import fuse
from app.search.schemas import SearchHit, SearchResponse

logger = logging.getLogger(__name__)
CANDIDATES = 50


async def _vector_hits(
    session: AsyncSession, query: str, document_id: int | None
) -> tuple[list[fulltext.RankedChunk], bool, str | None]:
    try:
        model = await current_model(session)
        vectors, _ = await embed(session, texts=[query], purpose="query")
        if not vectors:
            return [], False, "provider_error"
        hits = await vector.search(
            session, vectors[0], limit=CANDIDATES, document_id=document_id, model=model
        )
    except RoutingError:
        return [], False, "provider_unconfigured"
    except Exception as exc:  # noqa: BLE001 — provider 故障必须降级
        logger.warning("向量检索不可用，降级为纯全文检索: %s", exc)
        return [], False, "provider_error"
    return hits, bool(hits), None if hits else "index_incomplete"


async def search(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 20,
    document_id: int | None = None,
) -> SearchResponse:
    query = query.strip()
    if not query:
        return SearchResponse(query=query, expanded_terms=[], hits=[], vector_used=False)

    terms = await expand(session, query)
    text_hits = await fulltext.search(
        session, " ".join(terms), limit=CANDIDATES, document_id=document_id
    )
    vec_hits, vector_used, vector_reason = await _vector_hits(session, query, document_id)

    fused = fuse(text_hits, vec_hits, limit=limit)
    if not fused:
        return SearchResponse(
            query=query, expanded_terms=terms, hits=[], vector_used=vector_used,
            vector_unavailable_reason=vector_reason,
        )

    rows = (
        await session.execute(
            select(ClauseChunk, Clause, Document)
            .join(Clause, Clause.id == ClauseChunk.clause_id)
            .join(Document, Document.id == ClauseChunk.document_id)
            .where(ClauseChunk.id.in_([item.chunk_id for item in fused]))
        )
    ).all()
    by_id = {chunk.id: (chunk, clause, document) for chunk, clause, document in rows}

    hits = []
    for item in fused:
        found = by_id.get(item.chunk_id)
        if found is None:
            continue
        chunk, clause, document = found
        hits.append(
            SearchHit(
                chunk_id=item.chunk_id,
                clause_id=clause.id,
                document_id=document.id,
                document_title=document.title,
                citation_label=clause.citation_label,
                heading_path=clause.heading_path,
                text=chunk.text,
                score=item.score,
                rank_fulltext=item.rank_fulltext,
                rank_vector=item.rank_vector,
            )
        )

    return SearchResponse(
        query=query, expanded_terms=terms, hits=hits, vector_used=vector_used,
        vector_unavailable_reason=vector_reason,
    )
