"""关系提案的闸 3 与闸 4。

两个控制点都是库中既有数据，没有内容可编造；闸 4 在这里防的是判断错——
要求从两端各自的 statement 里逐字引出支撑这条关系的词句。只引一端
等于没说清楚这两者之间到底是什么关系。
"""

import math
from collections.abc import Collection
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.controls.models import Control
from app.llm.validation import normalize

_ENDS = (("from_control_id", "from_quote"), ("to_control_id", "to_quote"))


class RelationCitationValidator:
    def __init__(
        self,
        session: AsyncSession,
        *,
        control_ids: Collection[int],
        item_key: str = "relations",
        allowed_pairs: Collection[Collection[int]] | None = None,
    ) -> None:
        self._session = session
        self._control_ids = frozenset(control_ids)
        self._item_key = item_key
        # duplicates 通道一批并排 25 对、最多 50 个控制点。只查「两端都在本批次」
        # 挡不住模型把第 1 对的左边和第 7 对的右边连起来——那两个控制点从来不是
        # 相似度候选，也从未并排出现过。给了候选对就逐对查。
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
            return None if payload.get("insufficient_evidence") is True else "no relations were given"
        if payload.get("insufficient_evidence") is True:
            return "insufficient_evidence cannot be combined with relations"

        for entry in items:
            if not isinstance(entry, dict):
                return "a relation is malformed"
            for id_field, quote_field in _ENDS:
                control_id = entry.get(id_field)
                if type(control_id) is not int or control_id <= 0:
                    return f"invalid {id_field}: {control_id!r}"
                if control_id not in self._control_ids:
                    return f"control {control_id} is not in this batch"
                quote = entry.get(quote_field)
                if not isinstance(quote, str) or not normalize(quote):
                    return f"no quote was given for the control {control_id} side"
            if entry["from_control_id"] == entry["to_control_id"]:
                return "both ends of a relation cannot be the same control"
            ends = frozenset((entry["from_control_id"], entry["to_control_id"]))
            if self._allowed_pairs is not None and ends not in self._allowed_pairs:
                return (
                    f"controls {entry['from_control_id']} and {entry['to_control_id']} "
                    "are not a pair given side by side in this batch"
                )
            confidence = entry.get("confidence")
            if (
                type(confidence) not in (int, float)
                or isinstance(confidence, bool)
                or not math.isfinite(confidence)
                or not 0 <= confidence <= 1
            ):
                return f"invalid confidence: {confidence!r}"

        wanted = {entry[field] for entry in items for field, _ in _ENDS}
        statements = {
            row.id: row.statement or ""
            for row in await self._session.scalars(
                select(Control).where(Control.id.in_(wanted))
            )
        }
        for entry in items:
            for id_field, quote_field in _ENDS:
                control_id = entry[id_field]
                if control_id not in statements:
                    return f"control {control_id} does not exist"
                if normalize(entry[quote_field]) not in normalize(statements[control_id]):
                    return f"the quote was not found in the statement of control {control_id}"
        return None
