"""拿条款正文逐字校验引用。"""

from collections.abc import Collection
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.iam import audit
from app.llm.citation_repair import repair_quote
from app.llm.validation import clause_source, normalize

__all__ = ["ClauseCitationValidator", "clause_source", "normalize"]


class ClauseCitationValidator:
    def __init__(
        self, session: AsyncSession, *, document_id: int | None = None,
        clause_ids: Collection[int] | None = None, item_key: str = "controls",
        target_clause_ids: Collection[int] | None = None,
    ) -> None:
        self._session = session
        self._document_id = document_id
        self._clause_ids = frozenset(clause_ids) if clause_ids is not None else None
        self._item_key = item_key
        # 本次校验里修复过的引文，供调用方与测试查看；每条同时写进审计日志。
        self.repairs: list[dict[str, Any]] = []
        self._target_clause_ids = (
            frozenset(target_clause_ids) if target_clause_ids is not None else None
        )

    async def check(self, payload: dict[str, Any]) -> str | None:
        items = payload.get(self._item_key)
        if not isinstance(items, list):
            return f"{self._item_key} must be a list"
        if not items:
            return None if payload.get("insufficient_evidence") is True else "No evidence supplied"
        if payload.get("insufficient_evidence") is True:
            return "insufficient_evidence cannot accompany proposals"
        citations: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                return "Invalid proposal"
            sources = item.get("citations")
            if not isinstance(sources, list) or not sources:
                return "the proposal gives no citations"
            for source in sources:
                if not isinstance(source, dict):
                    return "Invalid citation"
                cid, quote = source.get("clause_id"), source.get("quote")
                if type(cid) is not int or cid <= 0:
                    return f"Invalid clause_id: {cid!r}"
                if not isinstance(quote, str) or not normalize(quote):
                    return f"the citation of clause {cid} gives no quote"
                citations.append(source)
            if self._target_clause_ids is not None and not any(
                source["clause_id"] in self._target_clause_ids for source in sources
            ):
                return "each control must cite at least one target clause, not only ancestor context"
        wanted = {c["clause_id"] for c in citations}
        clauses = {
            c.id: c for c in await self._session.scalars(
                select(Clause).where(Clause.id.in_(wanted))
            )
        }
        for source in citations:
            cid = source["clause_id"]
            clause = clauses.get(cid)
            if clause is None:
                return f"cited clause {cid} does not exist"
            if self._document_id is not None and clause.document_id != self._document_id:
                return f"cited clause {cid} is not in the document being extracted"
            if self._clause_ids is not None and cid not in self._clause_ids:
                return f"cited clause {cid} is not in this batch"
            if normalize(source["quote"]) not in normalize(clause_source(clause)):
                fixed = repair_quote(source["quote"], clause_source(clause))
                if fixed is None:
                    return f"the quote was not found in the text of clause {cid}"
                await self._record_repair("clause", cid, source["quote"], fixed)
                source["quote"] = fixed.quote
        return None

    async def _record_repair(self, entity: str, entity_id: int, original: str, fixed: Any) -> None:
        entry = {
            "entity": entity, "entity_id": entity_id, "original": original,
            "repaired": fixed.quote, "ratio": fixed.ratio, "edited_tokens": fixed.edited_tokens,
        }
        self.repairs.append(entry)
        await audit.record(
            self._session, user=None, action="citation.repair", entity_type=entity,
            entity_id=entity_id, before={"quote": original},
            after={"quote": fixed.quote, "ratio": fixed.ratio, "edited_tokens": fixed.edited_tokens},
        )
