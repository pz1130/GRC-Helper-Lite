"""成熟度建议的输入包：把三路信号收拢成一份可以送进提示词的结构。

设计 §396 定的三路输入——覆盖度、实施状态、证据新鲜度——分别躺在
`mappings`、`implementations`、`evidence_items` 三张表里。这里只负责取数和
整形，不做判断：给几分是模型的事，取哪些数是这里的事。

**证据那一路会缺席。** 机构刚部署完、证据类型都还没建的时候，
`evidence_items` 是空的。这时 `evidence` 是 None，而不是 0——
"还没开始登记证据"和"登记了但全过期"是两件事，混在一起会让模型把
前者当成后者扣分，而正确的做法是在理由里说"这一路没有数据"。
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.controls.models import Control, active_controls
from app.environment.models import Implementation
from app.evidence.freshness import display_status
from app.evidence.models import EvidenceItem
from app.frameworks.models import FrameworkItem, Mapping


@dataclass(frozen=True)
class EvidenceCounts:
    valid: int
    expired: int
    other: int


@dataclass(frozen=True)
class ControlContext:
    control_code: str
    title: str
    statement: str
    mapping_strength: str
    implementation_status: str | None
    how_enforced: str | None
    implementation_tally: dict[str, int] | None
    evidence: EvidenceCounts | None


@dataclass(frozen=True)
class ItemContext:
    framework_item_id: int
    code: str
    title: str
    description: str
    controls: list[ControlContext]


@dataclass(frozen=True)
class SuggestionInputs:
    items: list[ItemContext]
    evidence_available: bool


@dataclass(frozen=True)
class _Consolidated:
    """一个控制点的多条实施记录收成一条能进提示词的说法。"""

    status: str
    how_enforced: str | None
    tally: dict[str, int]


# 成熟度评估要看的是「还有哪个资产没落地」，所以取最保守的那一条。
# not_applicable 不参与排序：它是"这个资产上不适用"，不是"还没做"，
# 让它压低整体状态会把不适用当成缺口。全是 NA 时才报 NA。
_SEVERITY = {"planned": 0, "partial": 1, "implemented": 2}


def _consolidate(rows: list[Implementation]) -> _Consolidated | None:
    if not rows:
        return None
    tally: dict[str, int] = {}
    for row in rows:
        key = str(row.status)
        tally[key] = tally.get(key, 0) + 1
    ranked = [row for row in rows if str(row.status) in _SEVERITY]
    if not ranked:
        return _Consolidated(status=str(rows[0].status), how_enforced=None, tally=tally)
    # 同级之间按 id 定序，保证同一份数据每次给出同一条 how_enforced。
    worst = min(ranked, key=lambda row: (_SEVERITY[str(row.status)], row.id))
    return _Consolidated(
        status=str(worst.status),
        how_enforced=str(worst.how_enforced) if worst.how_enforced else None,
        tally=tally,
    )


async def build_inputs(
    session: AsyncSession,
    *,
    framework_id: int,
    now: datetime | None = None,
) -> SuggestionInputs:
    moment = now or datetime.now(UTC)

    items = list(
        await session.scalars(
            select(FrameworkItem)
            .where(FrameworkItem.framework_id == framework_id)
            .order_by(FrameworkItem.order_index, FrameworkItem.id)
        )
    )

    rows = list(
        await session.execute(
            select(Mapping.framework_item_id, Control, Mapping.strength)
            .join(Control, Control.id == Mapping.control_id)
            .where(
                Mapping.framework_item_id.in_([item.id for item in items] or [0]),
                # 合并掉的控制点不该再算进覆盖度（active_controls 是统一起点）
                Control.id.in_(select(active_controls().subquery().c.id)),
            )
        )
    )

    control_ids = {control.id for _, control, _ in rows}
    # 一个控制点每个技术资产一行（唯一约束是 control_id + tech_asset_id），
    # 所以这里必须按 control_id 收成**列表**。收成字典会留下这次 SELECT 恰好
    # 最后返回的那条，而查询没有 ORDER BY——同一份数据两次跑能喂进不同的状态。
    by_control: dict[int, list[Implementation]] = {}
    for row in await session.scalars(
        select(Implementation).where(Implementation.control_id.in_(control_ids or {0}))
    ):
        by_control.setdefault(row.control_id, []).append(row)
    implementations = {
        control_id: _consolidate(rows) for control_id, rows in by_control.items()
    }

    # 证据库整体是否有内容——决定第三路输入在不在，而不是某个控制点有没有证据。
    evidence_available = bool(await session.scalar(select(func.count()).select_from(EvidenceItem)))
    evidence_by_control: dict[int, EvidenceCounts] = {}
    if evidence_available:
        evidence_rows = list(
            await session.scalars(
                select(EvidenceItem).where(EvidenceItem.control_id.in_(control_ids or {0}))
            )
        )
        tally: dict[int, list[int]] = {}
        for row in evidence_rows:
            bucket = tally.setdefault(row.control_id, [0, 0, 0])
            status = display_status(row.status, row.valid_until, moment)
            index = 0 if status == "valid" else 1 if status == "expired" else 2
            bucket[index] += 1
        evidence_by_control = {
            control_id: EvidenceCounts(valid=counts[0], expired=counts[1], other=counts[2])
            for control_id, counts in tally.items()
        }

    grouped: dict[int, list[ControlContext]] = {}
    for item_id, control, strength in rows:
        implementation = implementations.get(control.id)
        grouped.setdefault(item_id, []).append(
            ControlContext(
                control_code=control.code,
                title=control.title,
                statement=control.statement,
                mapping_strength=str(strength),
                implementation_status=implementation.status if implementation else None,
                how_enforced=implementation.how_enforced if implementation else None,
                implementation_tally=implementation.tally if implementation else None,
                evidence=(
                    evidence_by_control.get(control.id, EvidenceCounts(0, 0, 0))
                    if evidence_available
                    else None
                ),
            )
        )

    return SuggestionInputs(
        items=[
            ItemContext(
                framework_item_id=item.id,
                code=item.code,
                title=item.title,
                description=item.description,
                controls=sorted(
                    grouped.get(item.id, []), key=lambda c: c.control_code
                ),
            )
            for item in items
        ],
        evidence_available=evidence_available,
    )
