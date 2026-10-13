"""Postgres 全文检索。

语料为纯英文，用 english 配置提供词干还原；中文跨语言检索由查询侧扩展负责。
"""

import re
from dataclasses import dataclass

from sqlalchemy import String, cast, func, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.clauses.models import ClauseChunk
from app.ingest.models import superseded_document_ids

_CONFIG = "english"
# 引号短语，或独立的 -排除 词：用户在显式表达"我要精确"
_PRECISION_SYNTAX = re.compile(r'"|(?:^|\s)-\S')


@dataclass(frozen=True)
class RankedChunk:
    chunk_id: int
    rank: int
    score: float


def _build_tsquery(query: str) -> ColumnElement:
    """默认 OR，仅在用户显式要求精确时才用 AND。

    websearch_to_tsquery 是**合取**语义：所有词都必须命中。实测该语料上
    "how are privileged accounts stored and rotated" 返回 0 条——只因为
    "rotated" 一个词在语料里不存在，整句就归零。而问句正是审计员和同事
    实际会用的查询形状。

    后果不止是少几条：全文这一路对问句返回空列表，RRF 就只剩单边输入，
    混合检索退化成纯向量检索。

    所以默认走 OR + ts_rank 排序——这是 Lucene/Elasticsearch 的默认行为，
    AND 才是异常。用户打了引号或减号时，说明他要的就是精确，那才用
    websearch_to_tsquery。
    """
    if _PRECISION_SYNTAX.search(query):
        return func.websearch_to_tsquery(_CONFIG, query)

    # plainto_tsquery 已经做好词干还原、停用词剔除和词素转义，
    # 只需把它的 & 换成 | 即可得到析取式。
    return cast(
        func.replace(cast(func.plainto_tsquery(_CONFIG, query), String), " & ", " | "),
        TSQUERY,
    )


async def search(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 50,
    document_id: int | None = None,
) -> list[RankedChunk]:
    if not query.strip():
        return []

    tsquery = _build_tsquery(query)
    score = func.ts_rank(ClauseChunk.tsv, tsquery)
    stmt = (
        select(ClauseChunk.id, score.label("score"))
        .where(ClauseChunk.tsv.op("@@")(tsquery))
        .order_by(score.desc(), ClauseChunk.id)
        .limit(limit)
    )
    if document_id is not None:
        stmt = stmt.where(ClauseChunk.document_id == document_id)
    else:
        # 不指定文档时只搜现行制度；明确在旧版里搜是查历史，上面照常放行。
        stmt = stmt.where(ClauseChunk.document_id.notin_(superseded_document_ids()))

    rows = (await session.execute(stmt)).all()
    return [
        RankedChunk(chunk_id=row.id, rank=position, score=float(row.score))
        for position, row in enumerate(rows, start=1)
    ]
