"""冲突检测的第一级候选生成——全程无 AI，只有 SQL 与向量查询。

与 M6 的 duplicate_pairs 同源，两处不同：
- 阈值更低。0.90 是对 duplicates 校准的，那要求「几乎是同一条」；
  冲突只要求「在谈同一件事」。
- 只留跨文件的对。同一份文件内部的措辞差异是编辑问题，不是制度冲突。
"""

from collections.abc import Mapping
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.clauses.models import Clause
from app.controls.models import ControlSource
from app.ingest.models import superseded_document_ids
from app.relations.clustering import Pair
from app.relations.models import ControlEmbedding


class ClauseRef(NamedTuple):
    clause_id: int
    document_title: str
    citation_label: str
    text: str

TOP_K_NEIGHBOURS = 20
# 146 个控制点的实测：top_k=8 只留下 300 个跨文件候选，而
# top_k=20 保留 658 个。冲突条款未必是最相似的前 8 条，因此用 20 换取召回；
# 第二级同时支持 max_batches 有界抽样，无需为验收一次跑完全量。
MIN_SIMILARITY = 0.85


async def documents_of(session: AsyncSession) -> dict[int, set[int]]:
    """控制点 → 它的支撑条款所在的文件 id 集合。"""
    rows = await session.execute(
        select(ControlSource.control_id, Clause.document_id)
        .join(Clause, Clause.id == ControlSource.clause_id)
        # 旧版不算一侧：它和新版之间的「差异」是修订，不是两份制度互相矛盾。
        .where(Clause.document_id.notin_(superseded_document_ids()))
        .order_by(ControlSource.control_id)
    )
    mapping: dict[int, set[int]] = {}
    for control_id, document_id in rows:
        mapping.setdefault(control_id, set()).add(document_id)
    return mapping


def keep_cross_document(
    pairs: list[Pair], documents: Mapping[int, set[int]]
) -> list[Pair]:
    """只留下两端落在不同文件的对。

    两端都没有支撑条款、或其中一端没有，一律丢弃——第二级要送原文，
    没有条款就没有原文可送。
    """
    kept: list[Pair] = []
    for pair in pairs:
        low = documents.get(pair.low) or set()
        high = documents.get(pair.high) or set()
        if not low or not high:
            continue
        # 任意一侧有对方没有的文件，就算跨文件。横跨多份文件的控制点也照此判。
        if low - high or high - low:
            kept.append(pair)
    return kept


async def cross_document_pairs(
    session: AsyncSession,
    *,
    top_k: int = TOP_K_NEIGHBOURS,
    min_similarity: float = MIN_SIMILARITY,
) -> list[Pair]:
    """每个控制点取 top-k 近邻，过阈值、去重、只留跨文件的。

    行序与并列名次都定死：批次指纹是对渲染后的 prompt 算的，顺序一变，
    重试时的检查点就全部落空，整轮重跑并再提一遍同样的对。
    """
    rows = list(
        await session.scalars(
            select(ControlEmbedding)
            .where(ControlEmbedding.embedding.is_not(None))
            .order_by(ControlEmbedding.control_id)
        )
    )
    other = aliased(ControlEmbedding)
    best: dict[tuple[int, int], float] = {}
    for row in rows:
        distance = other.embedding.cosine_distance(row.embedding)
        neighbours = await session.execute(
            select(other.control_id, distance.label("distance"))
            .where(
                other.embedding.is_not(None),
                other.control_id != row.control_id,
                # 跨模型的余弦距离没有意义，而换模型时的回填是逐批落盘的。
                other.embedding_model.is_not_distinct_from(row.embedding_model),
            )
            .order_by(distance, other.control_id)
            .limit(top_k)
        )
        for control_id, raw in neighbours:
            similarity = 1.0 - float(raw)
            if similarity < min_similarity:
                continue
            key = (min(row.control_id, control_id), max(row.control_id, control_id))
            best[key] = max(best.get(key, 0.0), similarity)

    ordered = [
        Pair(low, high, score)
        # 同分时按 (low, high) 定序，否则名次取决于堆扫描顺序。
        for (low, high), score in sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return keep_cross_document(ordered, await documents_of(session))


async def clause_bundles(
    session: AsyncSession, pairs: list[Pair]
) -> dict[int, list[ClauseRef]]:
    """候选对涉及的每个控制点 → 它的全部支撑条款原文。

    一次查完再按控制点分组，不在循环里查库——候选可达上千对。
    """
    from app.ingest.models import Document

    wanted = {control_id for pair in pairs for control_id in (pair.low, pair.high)}
    if not wanted:
        return {}
    rows = await session.execute(
        select(
            ControlSource.control_id,
            Clause.id,
            Document.title,
            Clause.citation_label,
            Clause.text,
        )
        .join(Clause, Clause.id == ControlSource.clause_id)
        .join(Document, Document.id == Clause.document_id)
        .where(
            ControlSource.control_id.in_(wanted),
            Clause.document_id.notin_(superseded_document_ids()),
        )
        # 行序定死：批次指纹对 prompt 取，顺序一变检查点全落空。
        .order_by(ControlSource.control_id, Clause.id)
    )
    bundles: dict[int, list[ClauseRef]] = {}
    for control_id, clause_id, title, label, text in rows:
        bundles.setdefault(control_id, []).append(
            ClauseRef(clause_id, title, label, text or "")
        )
    return bundles
