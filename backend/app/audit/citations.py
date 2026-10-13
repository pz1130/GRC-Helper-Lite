import re
from collections.abc import Collection, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.llm.validation import clause_source, normalize


class AnswerCitationValidator:
    def __init__(
        self,
        session: AsyncSession,
        *,
        allowed_clause_ids: Collection[int],
        allowed_control_ids: Collection[int] | None = None,
        allowed_evidence_ids: Collection[int] | None = None,
        require_quotes: bool = True,
        clause_labels: Mapping[int, str] | None = None,
    ) -> None:
        self._session = session
        # 只在生成时传：提示词给模型的是库内 clause_id，它常把这个号写进正文。
        # 接受已有提案时不查——旧答复里的内部号由审核人改，不能因此卡住接受。
        self._clause_labels = clause_labels
        self._allowed_clauses = frozenset(allowed_clause_ids)
        self._allowed_controls = (
            frozenset(allowed_control_ids) if allowed_control_ids is not None else None
        )
        self._allowed_evidence = (
            frozenset(allowed_evidence_ids) if allowed_evidence_ids is not None else None
        )
        self._require_quotes = require_quotes

    async def check(self, payload: dict[str, Any]) -> str | None:
        citations = payload.get("citations")
        if not isinstance(citations, list) or not citations:
            return "the answer must cite at least one internal clause"
        wanted: set[int] = set()
        for citation in citations:
            if not isinstance(citation, dict):
                return "an answer citation is malformed"
            clause_id, quote = citation.get("clause_id"), citation.get("quote")
            if type(clause_id) is not int or clause_id not in self._allowed_clauses:
                return f"clause {clause_id!r} is not among the retrieved results"
            if self._require_quotes and (not isinstance(quote, str) or not normalize(quote)):
                return f"clause {clause_id} has no quote"
            wanted.add(clause_id)
        for field, allowed, label in (
            ("cited_control_ids", self._allowed_controls, "control"),
            ("suggested_evidence_ids", self._allowed_evidence, "evidence"),
        ):
            values = payload.get(field)
            if allowed is None:
                continue
            if not isinstance(values, list) or any(type(value) is not int for value in values):
                return f"a {label} citation is malformed"
            outside = set(values) - allowed
            if outside:
                return f"{label} {min(outside)} is not among the retrieved results"
        if self._clause_labels is not None:
            reason = _internal_clause_id_reason(payload, self._clause_labels)
            if reason:
                return reason
        if not self._require_quotes:
            return None
        clauses = {
            # 含标题：段落级编号的文档里一句话会被劈在标题与正文之间
            clause.id: clause_source(clause)
            for clause in await self._session.scalars(select(Clause).where(Clause.id.in_(wanted)))
        }
        for citation in citations:
            clause_id = citation["clause_id"]
            if clause_id not in clauses:
                return f"clause {clause_id} does not exist"
            if normalize(citation["quote"]) not in normalize(clauses[clause_id]):
                return f"the quote was not found in the text of clause {clause_id}"
        return None


# 「Clause 63」「clause_id: 63」「clause ID #63」。后面紧跟 .数字 的是条款号（4.2），不是内部号。
_CLAUSE_ID_MENTION = re.compile(r"\bclause(?:[\s_-]*id)?[\s#:=]*(\d+)(?![.\d])", re.IGNORECASE)


def _internal_clause_id_reason(payload: dict[str, Any], labels: Mapping[int, str]) -> str | None:
    """正文或缺口说明里提到了给模型的内部 clause_id，而不是读者认得的条款号。

    只认本次提供过的 id；和某条条款号恰好相同的数字（条款号就是「63」）不算。
    """
    shown = set(labels.values())
    for field in ("body", "gap_notes"):
        text = payload.get(field)
        if not isinstance(text, str):
            continue
        for match in _CLAUSE_ID_MENTION.finditer(text):
            number = int(match.group(1))
            if number in labels and match.group(1) not in shown:
                return (
                    f"the answer refers to internal clause ID {number}; refer to the clause by "
                    f'its document title and clause number (for example "{labels[number]}") '
                    "instead"
                )
    return None


def _best_excerpt(text: str, context: str) -> str:
    compact = " ".join(text.split())
    parts = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", compact) if part.strip()]
    if not parts:
        return compact
    terms = {term.casefold() for term in re.findall(r"[\w-]{3,}", context)}

    def score(part: str) -> tuple[int, int]:
        words = {term.casefold() for term in re.findall(r"[\w-]{3,}", part)}
        return len(terms & words), -len(part)

    return max(parts, key=score)[:600]


async def ground_answer_citations(
    session: AsyncSession, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    ids = list(dict.fromkeys(citation["clause_id"] for citation in payload["citations"]))
    clauses = {
        clause.id: clause_source(clause)
        for clause in await session.scalars(select(Clause).where(Clause.id.in_(ids)))
    }
    return [
        {
            "clause_id": clause_id,
            "quote": _best_excerpt(clauses[clause_id], payload["body"]),
        }
        for clause_id in ids
        if clause_id in clauses
    ]
