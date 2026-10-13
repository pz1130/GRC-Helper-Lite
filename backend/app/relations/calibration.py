"""按语料自动标定 duplicates 通道的相似度阈值。

**免费的正样本从哪来**：制度语料里把同一段要求抄进两份文档是常态，于是会出现
「措辞几乎一样」的控制点对。用归一化词集的 Jaccard 找出它们，不需要人工标注。

**两个必须避开的坑**（都是在真实语料上撞出来的）：

1. `statement` **逐字相同**的对，embedding 必然完全相同、相似度恒为 1.000。
   只拿它们标定会得出一个高得离谱的阈值——某语料上是 0.96，而该语料真实的
   近重复低到 0.942，按 0.96 走会把它漏掉。它们是退化样本，计入分布但说明不了下界。

2. 近重复的相似度**不是紧密聚集的**：同一语料上 42 对近重复跨 0.423–0.998，
   其中两对词汇高度重叠却语义无关。取 `min` 会被这种离群点拖到地板。

所以取**低分位数**而不是最小值，并如实报告有几对正样本落在阈值之下（即会被漏掉）。
"""

import math
import re
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.controls.models import active_controls
from app.relations.models import ControlEmbedding

# 词集重叠到这个程度就认为「在说同一件事」。0.8 是紧的：更松会混进大量
# 只是共用行业词汇的对，把正样本污染成噪声。
MIN_JACCARD = 0.80
# 阈值保留这一比例的正样本。不用 min：离群点会把阈值拖到地板。
RETAIN = 0.90
MARGIN = 0.04
MIN_GROUND_TRUTH_PAIRS = 5
MIN_STATEMENT_CHARS = 40

_WORD = re.compile(r"[a-z0-9]+")


def tokens(text: str | None) -> frozenset[str]:
    return frozenset(_WORD.findall((text or "").lower()))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def quantile(sorted_values: list[float], q: float) -> float:
    """线性插值分位数。sorted_values 必须已升序且非空。"""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


@dataclass(frozen=True)
class Calibration:
    pairs: list[tuple[str, str, float, bool]]   # a, b, 余弦, 是否逐字相同
    recommended: float | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        below = (
            [p for p in self.pairs if p[2] < self.recommended]
            if self.recommended is not None else []
        )
        return {
            "ground_truth_pairs": [
                {"a": a, "b": b, "similarity": round(s, 4), "identical_statement": same}
                for a, b, s, same in self.pairs
            ],
            "recommended_min_similarity": self.recommended,
            # 如实说明这个阈值会漏掉哪几对已知正样本——分位数法必然漏掉一些，
            # 藏起来就等于假装它没有代价。
            "ground_truth_below_threshold": [
                {"a": a, "b": b, "similarity": round(s, 4)} for a, b, s, _ in below
            ],
            "reason": self.reason,
        }


async def _candidates(
    session: AsyncSession,
) -> tuple[list[tuple[str, str, float, bool]], int]:
    """返回 (已打分的正样本, 因缺向量而跳过的对数)。

    缺向量是可操作的状态——先补算再标定即可，所以要和「语料里本来就没有正样本」
    区分开，而不是合并成一句「找不到」。
    """
    controls = [
        c for c in await session.scalars(active_controls())
        if c.statement and len(c.statement.strip()) > MIN_STATEMENT_CHARS
    ]
    vectors = {
        row.control_id: row.embedding
        for row in await session.scalars(
            select(ControlEmbedding).where(ControlEmbedding.embedding.is_not(None))
        )
    }
    words = {c.id: tokens(c.statement) for c in controls}
    by_id = {c.id: c for c in controls}

    found: list[tuple[str, str, float, bool]] = []
    unembedded = 0
    for a, b in combinations(sorted(by_id), 2):
        if jaccard(words[a], words[b]) < MIN_JACCARD:
            continue
        # 没向量化的跳过，不能当成相似度 0——那会把阈值拉到地板。
        if a not in vectors or b not in vectors:
            unembedded += 1
            continue
        found.append((
            by_id[a].code, by_id[b].code, cosine(vectors[a], vectors[b]),
            by_id[a].statement == by_id[b].statement,
        ))
    return found, unembedded


async def calibrate(session: AsyncSession) -> Calibration:
    pairs, unembedded = await _candidates(session)
    if len(pairs) < MIN_GROUND_TRUTH_PAIRS:
        if unembedded:
            return Calibration(
                pairs, None,
                f"找到 {len(pairs) + unembedded} 对措辞高度重合的控制点，"
                f"但其中 {unembedded} 对尚未向量化，可用样本不足 "
                f"{MIN_GROUND_TRUTH_PAIRS} 对。请先补算控制点向量再标定。",
            )
        return Calibration(
            pairs, None,
            f"本语料只找到 {len(pairs)} 对措辞高度重合的控制点"
            f"（Jaccard ≥ {MIN_JACCARD}，至少需要 {MIN_GROUND_TRUTH_PAIRS} 对），无法标定。"
            "duplicates 通道将沿用默认阈值，其结果仅供参考。",
        )

    scores = sorted(s for _, _, s, _ in pairs)
    cut = quantile(scores, 1 - RETAIN)
    recommended = round(max(0.0, min(1.0, cut - MARGIN)), 2)
    identical = sum(1 for *_, same in pairs if same)
    return Calibration(
        pairs, recommended,
        f"{len(pairs)} 对正样本（其中 {identical} 对 statement 逐字相同，相似度恒为 1.0，"
        f"计入分布但不决定下界），相似度落在 {scores[0]:.3f}–{scores[-1]:.3f}；"
        f"取保留 {RETAIN:.0%} 的分位点 {cut:.3f} 再留 {MARGIN} 余量。",
    )
