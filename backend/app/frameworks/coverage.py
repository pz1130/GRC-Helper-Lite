"""覆盖度与差距。"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.controls.models import Control, ControlSource
from app.frameworks.models import FrameworkItem, Mapping, MappingStrength
from app.ingest.models import DocStatus, Document

CLOSING = (MappingStrength.FULL, MappingStrength.PARTIAL)


@dataclass(frozen=True)
class CoverageRow:
    item_id: int
    code: str
    title: str
    level: int
    parent_id: int | None
    requirements: int
    covered: int
    # 已覆盖的要求里，关上它的控制点全都只剩已替换文档作出处的那几条。仍计入 covered。
    superseded_only: int = 0


@dataclass(frozen=True)
class GapRow:
    item_id: int
    code: str
    title: str
    has_supporting: bool


@dataclass(frozen=True)
class SupersededSupportRow:
    item_id: int
    code: str
    title: str
    controls: list[str]


def is_requirement(item: Any, has_children: bool) -> bool:
    """默认「叶子即要求」，允许 attributes.is_requirement 双向覆盖。"""
    explicit = (item.attributes or {}).get("is_requirement")
    if isinstance(explicit, bool):
        return explicit
    return not has_children


def _in_baseline(item: Any, baseline: str | None) -> bool:
    if baseline is None:
        return True
    baselines = (item.attributes or {}).get("baselines")
    return isinstance(baselines, list) and baseline in baselines


async def _load(
    session: AsyncSession, framework_id: int, baseline: str | None
) -> tuple[list[Any], dict[int, set[str]], set[int], dict[int, list[str]]]:
    items = list(await session.scalars(
        select(FrameworkItem)
        .where(FrameworkItem.framework_id == framework_id)
        .order_by(FrameworkItem.order_index, FrameworkItem.id)
    ))
    with_children = {item.parent_id for item in items if item.parent_id is not None}

    rows = await session.execute(
        select(Mapping.framework_item_id, Mapping.strength, Mapping.control_id)
        .join(FrameworkItem, FrameworkItem.id == Mapping.framework_item_id)
        .where(FrameworkItem.framework_id == framework_id)
    )
    strengths: dict[int, set[str]] = {}
    closers: dict[int, set[int]] = {}
    for item_id, strength, control_id in rows:
        value = strength.value if hasattr(strength, "value") else str(strength)
        strengths.setdefault(item_id, set()).add(value)
        if value in {s.value for s in CLOSING}:
            closers.setdefault(item_id, set()).add(control_id)
    stale = await _superseded_controls(session, {c for ids in closers.values() for c in ids})
    # 关上它的控制点**全都**只剩已替换出处：还有一个有现行依据（或本来就没有条款
    # 出处，比如 Excel 导入的）就不算。
    superseded_only = {
        item_id: sorted(stale[c] for c in ids)
        for item_id, ids in closers.items()
        if ids and all(c in stale for c in ids)
    }

    requirements = {
        item.id
        for item in items
        if is_requirement(item, item.id in with_children) and _in_baseline(item, baseline)
    }
    return items, strengths, requirements, superseded_only


async def _superseded_controls(session: AsyncSession, control_ids: set[int]) -> dict[int, str]:
    """出处全在已替换文档里的控制点 → 其代码。

    没有任何条款出处的控制点（Excel 导入的）不在其中：它从来没有文档依据，
    谈不上「依据作废」。
    """
    if not control_ids:
        return {}
    rows = await session.execute(
        select(ControlSource.control_id, Control.code, Document.status)
        .join(Control, Control.id == ControlSource.control_id)
        .join(Clause, Clause.id == ControlSource.clause_id)
        .join(Document, Document.id == Clause.document_id)
        .where(ControlSource.control_id.in_(control_ids))
    )
    codes: dict[int, str] = {}
    live: set[int] = set()
    for control_id, code, status in rows:
        codes[control_id] = code
        if status is not DocStatus.SUPERSEDED:
            live.add(control_id)
    return {control_id: code for control_id, code in codes.items() if control_id not in live}


def _closed(strengths: dict[int, set[str]], item_id: int) -> bool:
    return bool(strengths.get(item_id, set()) & {strength.value for strength in CLOSING})


async def summarize(
    session: AsyncSession, framework_id: int, *, baseline: str | None = None
) -> list[CoverageRow]:
    """每个节点汇总其子树内的已覆盖要求数 / 要求总数。"""
    items, strengths, requirements, superseded_only = await _load(session, framework_id, baseline)
    if not items:
        return []

    children: dict[int | None, list[Any]] = {}
    for item in items:
        children.setdefault(item.parent_id, []).append(item)

    totals: dict[int, tuple[int, int, int]] = {}

    def walk(item: Any) -> tuple[int, int, int]:
        total = 1 if item.id in requirements else 0
        closed = item.id in requirements and _closed(strengths, item.id)
        covered = 1 if closed else 0
        stale = 1 if closed and item.id in superseded_only else 0
        for child in children.get(item.id, []):
            child_total, child_covered, child_stale = walk(child)
            total += child_total
            covered += child_covered
            stale += child_stale
        totals[item.id] = (total, covered, stale)
        return total, covered, stale

    for root in children.get(None, []):
        walk(root)

    return [
        CoverageRow(
            item_id=item.id,
            code=item.code,
            title=item.title,
            level=item.level,
            parent_id=item.parent_id,
            requirements=totals[item.id][0],
            covered=totals[item.id][1],
            superseded_only=totals[item.id][2],
        )
        for item in items
    ]


async def gaps(
    session: AsyncSession, framework_id: int, *, baseline: str | None = None
) -> list[GapRow]:
    """无任何 full/partial 映射的要求项。仅有 supporting 不消差距。"""
    items, strengths, requirements, _ = await _load(session, framework_id, baseline)
    return [
        GapRow(
            item_id=item.id,
            code=item.code,
            title=item.title,
            has_supporting=MappingStrength.SUPPORTING.value in strengths.get(item.id, set()),
        )
        for item in items
        if item.id in requirements and not _closed(strengths, item.id)
    ]


async def superseded_support(
    session: AsyncSession, framework_id: int, *, baseline: str | None = None
) -> list[SupersededSupportRow]:
    """已覆盖、但关上它的控制点全都只剩已替换文档作出处的要求项。

    新版删掉某条要求、旧版被替换后，依据它的控制点与映射都还在，覆盖度照样算
    已覆盖。按 2026-09-23 的决定不自动降级（已确认的数据只由人改），而是列出来，
    交给人决定退掉控制点、改映射，还是在新版里补出处。
    """
    items, strengths, requirements, superseded_only = await _load(session, framework_id, baseline)
    return [
        SupersededSupportRow(
            item_id=item.id, code=item.code, title=item.title, controls=superseded_only[item.id],
        )
        for item in items
        if item.id in requirements and _closed(strengths, item.id) and item.id in superseded_only
    ]
