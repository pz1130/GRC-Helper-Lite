"""映射候选预筛（K16/K17）：每个框架项先挑出最相关的 TOP_K 个控制点再交给模型。

原先每批都把**全部**控制点塞进提示词：库一大就撞 60000 字符上限直接失败，
而且模型在几百个控制点里挑，召回很低（OLIR 实测 0.15）。

两路排序、RRF 融合：
· 关键词：控制点「标题 + 编号 + 陈述」上的 BM25。纯 Python、确定性、零成本，
  任何时候都可用。
· 向量：控制点已有的 embedding（control_embeddings，当前 embedding 模型）
  对框架项文本的余弦相似度。框架项向量现算一次；没有配置 embedding、调用
  失败或控制点还没建索引时，这一路为空，自动退化为纯关键词。

这里**不用**判断模型。用 Jev 排序是另一条可选路（`jev_prefilter.py`，默认关闭：控制点要
发给外部服务，须先过数据政策，见 K17）；它失败、超时或未配置时，整轮回退到这里。

已知取舍：模型只看得到候选，所以「没有控制点满足」只在候选范围内成立——
真正的覆盖缺口仍需人工确认（见 OPERATIONS / KNOWN_ISSUES K16）。
"""

import logging
import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

TOP_K = 20
RRF_K = 60
# 向量一路的 RRF 权重。OLIR 离线评估（evals/，embo-01）：关键词前 20 召回 0.39、
# 纯向量 0.23、等权融合 0.38、向量权重 0.5 时 0.40——向量单独偏弱，只作补充。
VECTOR_WEIGHT = 0.5
_BM25_K1 = 1.2
_BM25_B = 0.75
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "their", "this", "to", "was", "were", "which", "with", "within", "will", "shall", "must", "should", "may", "all", "any", "each", "such", "other", "organization", "organizational", "organizations", "system", "systems"]
)


def _stem(token: str) -> str:
    for suffix in ("ations", "ation", "ments", "ment", "ing", "ies", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return token


def tokenize(text: str) -> list[str]:
    return [
        _stem(token) for token in _WORD.findall(text.casefold())
        if len(token) > 2 and token not in _STOP
    ]


def control_text(control: Any) -> str:
    return f"{control.title or ''} {control.code or ''} {control.statement or ''}"


def item_text(item: Any) -> str:
    return f"{item.title or ''} {item.description or ''}"


class KeywordIndex:
    """BM25，文档 = 控制点。"""

    def __init__(self, docs: Sequence[tuple[int, str]]) -> None:
        self._ids = [doc_id for doc_id, _ in docs]
        self._tfs = [Counter(tokenize(text)) for _, text in docs]
        self._lens = [sum(tf.values()) for tf in self._tfs]
        self._avg = (sum(self._lens) / len(self._lens)) if self._lens else 0.0
        df: Counter[str] = Counter()
        for tf in self._tfs:
            df.update(tf.keys())
        n = len(docs)
        self._idf = {term: math.log(1 + (n - d + 0.5) / (d + 0.5)) for term, d in df.items()}

    def rank(self, query: str) -> list[int]:
        terms = set(tokenize(query))
        scored = []
        for idx, tf in enumerate(self._tfs):
            score = 0.0
            for term in terms:
                freq = tf.get(term)
                if not freq:
                    continue
                norm = 1 - _BM25_B + _BM25_B * self._lens[idx] / (self._avg or 1)
                score += self._idf[term] * freq * (_BM25_K1 + 1) / (freq + _BM25_K1 * norm)
            if score > 0:
                scored.append((score, self._ids[idx]))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [doc_id for _, doc_id in scored]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def vector_rank(query_vector: Sequence[float], control_vectors: dict[int, Sequence[float]]) -> list[int]:
    scored = sorted(
        ((_cosine(query_vector, vec), cid) for cid, vec in control_vectors.items()),
        key=lambda pair: (-pair[0], pair[1]),
    )
    return [cid for _, cid in scored]


def fuse(
    rankings: Sequence[list[int]], *, top_k: int = TOP_K, weights: Sequence[float] | None = None
) -> list[int]:
    scores: dict[int, float] = {}
    for n, ranking in enumerate(rankings):
        weight = weights[n] if weights else 1.0
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + weight / (RRF_K + rank + 1)
    return [cid for cid, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))][:top_k]


def rank_candidates(
    items: Sequence[tuple[int, str]],
    controls: Sequence[tuple[int, str]],
    *,
    item_vectors: dict[int, Sequence[float]] | None = None,
    control_vectors: dict[int, Sequence[float]] | None = None,
    top_k: int = TOP_K,
) -> dict[int, list[int]]:
    """每个框架项 → 前 top_k 个控制点 id（确定性；评估脚本与任务共用）。"""
    index = KeywordIndex(controls)
    result: dict[int, list[int]] = {}
    for item_id, text in items:
        rankings, weights = [index.rank(text)], [1.0]
        if item_vectors and control_vectors and item_id in item_vectors:
            rankings.append(vector_rank(item_vectors[item_id], control_vectors))
            weights.append(VECTOR_WEIGHT)
        result[item_id] = fuse(rankings, top_k=top_k, weights=weights)
    return result


async def load_vectors(
    session: AsyncSession, items: Sequence[tuple[int, str]], control_ids: Sequence[int]
) -> tuple[dict[int, list[float]], dict[int, list[float]]]:
    """向量这一路。任何失败都退化为空（纯关键词），不让映射因此失败。"""
    from app.indexing.embedder import current_model
    from app.llm.runner import embed
    from app.relations.models import ControlEmbedding

    try:
        model = await current_model(session)
        rows = await session.scalars(
            select(ControlEmbedding).where(
                ControlEmbedding.control_id.in_(list(control_ids)),
                ControlEmbedding.embedding_model == model,
                ControlEmbedding.embedding.is_not(None),
            )
        )
        control_vectors = {row.control_id: list(row.embedding) for row in rows}
        if not control_vectors or not items:
            return {}, control_vectors
        vectors, _ = await embed(session, texts=[text for _, text in items], purpose="query")
        if len(vectors) != len(items):
            return {}, control_vectors
        return {item_id: vec for (item_id, _), vec in zip(items, vectors, strict=True)}, control_vectors
    except Exception as exc:  # noqa: BLE001 —— 预筛降级，不挡映射
        logger.warning("映射预筛的向量一路不可用，退化为纯关键词: %s", exc)
        return {}, {}
