"""冲突提案的闸 3 与闸 4。

两条条款都是库中既有数据，没有内容可编造；闸 4 在这里防的是判断错——
要求从两条条款的原文里各自逐字引出打架的那句话。只引一端等于没说清
到底哪两句在冲突。
"""

import math
from collections.abc import Collection
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.llm.validation import clause_source, normalize

_ENDS = (("clause_a_id", "quote_a"), ("clause_b_id", "quote_b"))


class ConflictCitationValidator:
    def __init__(
        self,
        session: AsyncSession,
        *,
        clause_ids: Collection[int],
        item_key: str = "conflicts",
        allowed_pairs: Collection[Collection[int]] | None = None,
    ) -> None:
        self._session = session
        self._clause_ids = frozenset(clause_ids)
        self._item_key = item_key
        # 只查「两端都在本批次」挡不住模型把第 1 对的左边和第 7 对的右边连起来
        # ——那两条条款从来不是候选、也从未并排出现过。给了候选对就逐对查。
        self._allowed_pairs = (
            frozenset(frozenset(pair) for pair in allowed_pairs)
            if allowed_pairs is not None
            else None
        )

    async def check(self, payload: dict[str, Any]) -> str | None:
        items = payload.get(self._item_key)
        if not isinstance(items, list):
            return f"{self._item_key} must be a list"
        if not items:
            return None if payload.get("insufficient_evidence") is True else "no conflicts were given"
        if payload.get("insufficient_evidence") is True:
            return "insufficient_evidence cannot be combined with conflicts"

        for entry in items:
            if not isinstance(entry, dict):
                return "a conflict is malformed"
            for id_field, quote_field in _ENDS:
                clause_id = entry.get(id_field)
                if type(clause_id) is not int or clause_id <= 0:
                    return f"invalid {id_field}: {clause_id!r}"
                if clause_id not in self._clause_ids:
                    return f"clause {clause_id} is not in this batch"
                quote = entry.get(quote_field)
                if not isinstance(quote, str) or not normalize(quote):
                    return f"no quote was given for the clause {clause_id} side"
            if entry["clause_a_id"] == entry["clause_b_id"]:
                return "both sides of a conflict cannot be the same clause"
            ends = frozenset((entry["clause_a_id"], entry["clause_b_id"]))
            if self._allowed_pairs is not None and ends not in self._allowed_pairs:
                return (
                    f"clauses {entry['clause_a_id']} and {entry['clause_b_id']} "
                    "are not a pair given side by side in this batch"
                )
            topic = entry.get("topic")
            if not isinstance(topic, str) or not topic.strip():
                return "a conflict must give a topic"
            confidence = entry.get("confidence")
            if (
                type(confidence) not in (int, float)
                or isinstance(confidence, bool)
                or not math.isfinite(confidence)
                or not 0 <= confidence <= 1
            ):
                return f"invalid confidence: {confidence!r}"

        wanted = {entry[field] for entry in items for field, _ in _ENDS}
        texts = {
            # 对着条款的**全部文字**，含标题——理由见 llm.validation.clause_source
            row.id: clause_source(row)
            for row in await self._session.scalars(
                select(Clause).where(Clause.id.in_(wanted))
            )
        }
        for entry in items:
            for id_field, quote_field in _ENDS:
                clause_id = entry[id_field]
                if clause_id not in texts:
                    return f"clause {clause_id} does not exist"
                if normalize(entry[quote_field]) not in normalize(texts[clause_id]):
                    return f"the quote was not found in the text of clause {clause_id}"
        return None
