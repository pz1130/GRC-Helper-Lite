"""变更影响分析——不用 AI，不写库（spec §1 决定④）。

条款配对优先用 (number, heading_path) 精确匹配；跨解析器代际时，仅在一侧
缺编号时用 heading_path 回退。用向量相似度做模糊配对会更
"聪明"，但影响报告的价值在于可核对：一条「这两条是同一条」的判断若来自
相似度，读报告的人没法验证它对不对。宁可列出"未配对"让人自己看。
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.controls.models import Control, ControlSource
from app.errors import BadRequest, NotFound
from app.evidence.models import EvidenceItem
from app.frameworks.models import FrameworkItem, Mapping
from app.impact.schemas import (
    AffectedControlOut,
    AffectedEvidenceOut,
    AffectedMappingOut,
    ClauseChangeOut,
    ImpactOut,
    MatchedClauseOut,
    PreviousDocumentOut,
)
from app.ingest.models import Document


@dataclass(frozen=True)
class ClausePairing:
    matched: list[tuple[int, int]]   # (旧条款 id, 新条款 id)
    removed: list[int]               # 只在旧版里
    added: list[int]                 # 只在新版里


def _key(clause: Any) -> tuple[str, str]:
    return ((clause.number or "").strip(), (clause.heading_path or "").strip())


def pair_clauses(old: list[Any], new: list[Any]) -> ClausePairing:
    """先精确配对；剩余条款仅在一侧缺编号时按标题路径回退。"""
    old_by_key: dict[tuple[str, str], list[Any]] = {}
    for clause in sorted(old, key=lambda c: c.id):
        old_by_key.setdefault(_key(clause), []).append(clause)
    new_by_key: dict[tuple[str, str], list[Any]] = {}
    for clause in sorted(new, key=lambda c: c.id):
        new_by_key.setdefault(_key(clause), []).append(clause)

    matched: list[tuple[int, int]] = []
    removed: list[int] = []
    added: list[int] = []

    for key in sorted(old_by_key.keys() | new_by_key.keys()):
        lefts = old_by_key.get(key, [])
        rights = new_by_key.get(key, [])
        paired = min(len(lefts), len(rights))
        matched.extend((lefts[i].id, rights[i].id) for i in range(paired))
        removed.extend(clause.id for clause in lefts[paired:])
        added.extend(clause.id for clause in rights[paired:])

    # 旧解析器没有 number，但 heading_path 稳定。回退只允许一侧缺号；
    # 两侧都有编号时，编号改动仍然是一删一增。
    old_remaining = {clause.id: clause for clause in old if clause.id in removed}
    new_remaining = {clause.id: clause for clause in new if clause.id in added}
    for old_id in sorted(old_remaining):
        left = old_remaining[old_id]
        path = (left.heading_path or "").strip()
        if not path:
            continue
        candidates = [
            right for right in new_remaining.values()
            if (right.heading_path or "").strip() == path
            and (not (left.number or "").strip() or not (right.number or "").strip())
        ]
        if not candidates:
            continue
        right = min(candidates, key=lambda clause: clause.id)
        matched.append((old_id, right.id))
        del old_remaining[old_id]
        del new_remaining[right.id]

    # 更新的解析器会补出旧代漏掉的中间章节，导致完整路径多一层。
    # 只在叶子标题两侧都唯一、且一侧缺编号时配对，避免同名章节误配。
    old_by_leaf: dict[str, list[Any]] = {}
    new_by_leaf: dict[str, list[Any]] = {}
    for clause in old_remaining.values():
        old_by_leaf.setdefault(_leaf_heading(clause), []).append(clause)
    for clause in new_remaining.values():
        new_by_leaf.setdefault(_leaf_heading(clause), []).append(clause)
    for leaf in sorted(old_by_leaf.keys() & new_by_leaf.keys()):
        lefts = old_by_leaf[leaf]
        rights = new_by_leaf[leaf]
        if not leaf or len(lefts) != 1 or len(rights) != 1:
            continue
        left, right = lefts[0], rights[0]
        if (left.number or "").strip() and (right.number or "").strip():
            continue
        matched.append((left.id, right.id))
        del old_remaining[left.id]
        del new_remaining[right.id]

    return ClausePairing(
        sorted(matched), sorted(old_remaining), sorted(new_remaining)
    )


def _normalise_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _leaf_heading(clause: Any) -> str:
    return (clause.heading_path or "").split("›")[-1].strip()


def _clause_out(clause: Clause) -> ClauseChangeOut:
    return ClauseChangeOut(
        clause_id=clause.id,
        citation_label=clause.citation_label,
        text=clause.text,
    )


async def change_impact(session: AsyncSession, document_id: int) -> ImpactOut:
    document = await session.get(Document, document_id)
    if document is None:
        raise NotFound("文档不存在", code="document.not_found")
    if document.supersedes_id is None:
        raise BadRequest("该文档没有上一版本", code="document.no_previous_version")

    previous = await session.get(Document, document.supersedes_id)
    if previous is None:
        raise NotFound("上一版本文档不存在", code="document.previous_version_not_found")

    # 并掉的条款行不删，正文已在胜者里。拿它们配对会报出「删了」「新增了」的幽灵条款，
    # 与条款树看到的不一致。
    old_clauses = list(
        await session.scalars(
            select(Clause)
            .where(Clause.document_id == previous.id, Clause.status != "merged")
            .order_by(Clause.id)
        )
    )
    new_clauses = list(
        await session.scalars(
            select(Clause)
            .where(Clause.document_id == document.id, Clause.status != "merged")
            .order_by(Clause.id)
        )
    )
    pairing = pair_clauses(old_clauses, new_clauses)
    parser_generation_mismatch = bool(
        old_clauses
        and new_clauses
        and not any((clause.number or "").strip() for clause in old_clauses)
        and any((clause.number or "").strip() for clause in new_clauses)
    )
    by_id = {clause.id: clause for clause in (*old_clauses, *new_clauses)}

    added = [_clause_out(by_id[clause_id]) for clause_id in pairing.added]
    removed = [_clause_out(by_id[clause_id]) for clause_id in pairing.removed]
    matched = []
    changed_old_ids: list[int] = []
    for old_id, new_id in pairing.matched:
        old_text = by_id[old_id].text or ""
        new_text = by_id[new_id].text or ""
        changed = _normalise_text(old_text) != _normalise_text(new_text)
        if changed:
            changed_old_ids.append(old_id)
        matched.append(MatchedClauseOut(
            old_clause_id=old_id,
            new_clause_id=new_id,
            citation_label=by_id[new_id].citation_label,
            text=new_text,
            old_text=old_text,
            changed=changed,
        ))

    affected_controls: list[AffectedControlOut] = []
    affected_mappings: list[AffectedMappingOut] = []
    affected_evidence: list[AffectedEvidenceOut] = []
    impacted_clause_ids = [*pairing.removed, *changed_old_ids]
    if impacted_clause_ids:
        controls = list(
            await session.scalars(
                select(Control)
                .where(
                    Control.id.in_(
                        select(ControlSource.control_id).where(
                            ControlSource.clause_id.in_(impacted_clause_ids)
                        )
                    )
                )
                .order_by(Control.id)
            )
        )
        affected_controls = [
            AffectedControlOut(id=control.id, code=control.code, title=control.title)
            for control in controls
        ]
        codes = {control.id: control.code for control in controls}
        control_ids = list(codes)
        if control_ids:
            mapping_rows = await session.execute(
                select(Mapping, FrameworkItem)
                .join(FrameworkItem, FrameworkItem.id == Mapping.framework_item_id)
                .where(Mapping.control_id.in_(control_ids))
                .order_by(Mapping.id)
            )
            affected_mappings = [
                AffectedMappingOut(
                    id=mapping.id,
                    control_id=mapping.control_id,
                    control_code=codes[mapping.control_id],
                    framework_item_code=item.code,
                )
                for mapping, item in mapping_rows
            ]
            evidence_rows = list(
                await session.scalars(
                    select(EvidenceItem)
                    .where(EvidenceItem.control_id.in_(control_ids))
                    .order_by(EvidenceItem.id)
                )
            )
            affected_evidence = [
                AffectedEvidenceOut(
                    id=item.id,
                    control_id=item.control_id,
                    control_code=codes[item.control_id],
                    title=item.title,
                )
                for item in evidence_rows
            ]

    return ImpactOut(
        previous_document=PreviousDocumentOut(
            id=previous.id, title=previous.title, version=previous.version
        ),
        parser_generation_mismatch=parser_generation_mismatch,
        added=added,
        removed=removed,
        matched=matched,
        affected_controls=affected_controls,
        affected_mappings=affected_mappings,
        affected_evidence=affected_evidence,
    )
