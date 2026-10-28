"""合并计划：只查不写。算出会转挂什么、会丢弃什么，供人决定后再执行。

丢弃项与阻断原因都带 `code` + `params`，句子由前端按语言组（词条在
`mergeReasons` 下，`make i18n-check` 把关）。`detail` 那句中文是前端缺词条时的
兜底，也原样进审计日志——审计要的是当时给人看的那句话。
"""

import hashlib
import json
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.controls.models import (
    Control,
    ControlRelation,
    ControlSource,
    canonicalize_relation_endpoints,
)
from app.environment.models import Implementation, TechAsset
from app.evidence.models import EvidenceItem
from app.frameworks.models import FrameworkItem, Mapping
from app.relations.models import ControlEmbedding
from app.risk.models import RiskEntry


@dataclass(frozen=True)
class Discard:
    table: str
    id: int
    detail: str
    code: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Blocker:
    code: str
    detail: str


@dataclass(frozen=True)
class MergePlan:
    loser_code: str
    winner_code: str
    moves: dict[str, int]
    discards: list[Discard]
    blockers: list[Blocker]


def plan_digest(plan: MergePlan) -> str:
    """计划的指纹：转挂多少、丢弃哪几行。

    预览时交给前端，执行时带回来与重算的计划比对。执行必须重算（不信客户端
    送来的计划），但重算结果若与预览不同，多丢的那几行没有人同意过。
    说明文字不进指纹——换语言、改措辞不该让一次合并失效。
    """
    canonical = {
        "loser": plan.loser_code,
        "winner": plan.winner_code,
        "moves": plan.moves,
        "discards": sorted([item.table, item.id] for item in plan.discards),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


_NOT_FOUND = Blocker(code="merge.blocked.not_found", detail="控制点不存在")
_SELF = Blocker(code="merge.blocked.self", detail="不能把控制点并入它自己")
_ALREADY_MERGED = Blocker(code="merge.blocked.already_merged", detail="已经合并的控制点不能再合并")


def _add_moves(moves: dict[str, int], table: str, n: int) -> None:
    if n:
        moves[table] = n


async def _load_and_blockers(
    session: AsyncSession, loser_id: int, winner_id: int
) -> tuple[Control | None, Control | None, list[Blocker]]:
    if loser_id == winner_id:
        control = await session.get(Control, loser_id)
        if control is None:
            return None, None, [_NOT_FOUND]
        blockers = [_SELF]
        if control.status == "merged":
            blockers.append(_ALREADY_MERGED)
        return control, control, blockers

    loser = await session.get(Control, loser_id)
    winner = await session.get(Control, winner_id)
    blockers: list[Blocker] = []
    if loser is None or winner is None:
        blockers.append(_NOT_FOUND)
    if (loser is not None and loser.status == "merged") or (
        winner is not None and winner.status == "merged"
    ):
        blockers.append(_ALREADY_MERGED)
    return loser, winner, blockers


async def _count_on_loser(session: AsyncSession, model: type, loser_id: int) -> int:
    n = await session.scalar(
        select(func.count()).select_from(model).where(model.control_id == loser_id)
    )
    return int(n or 0)


async def _plan_embeddings(session: AsyncSession, loser_id: int) -> list[Discard]:
    # 一条控制点一行向量；赢家的留着，输家的整行丢掉。
    rows = list(
        await session.scalars(
            select(ControlEmbedding)
            .where(ControlEmbedding.control_id == loser_id)
            .order_by(ControlEmbedding.id)
        )
    )
    return [
        Discard(
            table="control_embeddings",
            id=row.id,
            detail="输家向量丢弃，保留赢家的",
            code="merge.discard.embedding",
        )
        for row in rows
    ]


async def _plan_relations(
    session: AsyncSession, loser: Control, winner: Control
) -> tuple[int, list[Discard]]:
    # DUPLICATES 对称：赢家侧既有关系以规范形纳入集合，否则镜像比对必然漏判。
    occupied = {
        (*canonicalize_relation_endpoints(
            row.from_control_id, row.to_control_id, row.relation_type
        ), row.relation_type)
        for row in await session.scalars(
            select(ControlRelation).where(
                ControlRelation.from_control_id != loser.id,
                ControlRelation.to_control_id != loser.id,
            )
        )
    }
    rows = list(
        await session.scalars(
            select(ControlRelation)
            .where(
                or_(
                    ControlRelation.from_control_id == loser.id,
                    ControlRelation.to_control_id == loser.id,
                )
            )
            .order_by(ControlRelation.id)
        )
    )
    moved = 0
    discards: list[Discard] = []
    for row in rows:
        new_from = winner.id if row.from_control_id == loser.id else row.from_control_id
        new_to = winner.id if row.to_control_id == loser.id else row.to_control_id
        # 自环判定在规范化前后等价（规范化不改变相等性），维持 relation_self_loop。
        if new_from == new_to:
            discards.append(
                Discard(
                    table="control_relations",
                    id=row.id,
                    detail=f"{row.relation_type.value}：合并后会自环",
                    code="merge.discard.relation_self_loop",
                    params={"relation": row.relation_type.value},
                )
            )
            continue
        # DUPLICATES 对称：先规范化转挂后的端点再组键比对，否则镜像碰撞会被
        # 漏判成 move。与执行路径同一份规则，plan_digest 不发散。
        new_from, new_to = canonicalize_relation_endpoints(
            new_from, new_to, row.relation_type
        )
        key = (new_from, new_to, row.relation_type)
        if key in occupied:
            discards.append(
                Discard(
                    table="control_relations",
                    id=row.id,
                    detail=f"{row.relation_type.value}：转挂后与已有关系重复",
                    code="merge.discard.relation_duplicate",
                    params={"relation": row.relation_type.value},
                )
            )
        else:
            occupied.add(key)
            moved += 1
    return moved, discards


async def _plan_sources(
    session: AsyncSession, loser_id: int, winner_id: int
) -> tuple[int, list[Discard]]:
    winner_clauses = set(
        await session.scalars(
            select(ControlSource.clause_id).where(ControlSource.control_id == winner_id)
        )
    )
    rows = (
        await session.execute(
            select(ControlSource, Clause)
            .join(Clause, Clause.id == ControlSource.clause_id)
            .where(ControlSource.control_id == loser_id)
            .order_by(ControlSource.id)
        )
    ).all()
    moved = 0
    discards: list[Discard] = []
    for source, clause in rows:
        if source.clause_id in winner_clauses:
            discards.append(
                Discard(
                    table="control_sources",
                    id=source.id,
                    detail=f"条款 {clause.citation_label}：赢家已有",
                    code="merge.discard.source_duplicate",
                    params={"clause": clause.citation_label},
                )
            )
        else:
            moved += 1
    return moved, discards


async def _plan_implementations(
    session: AsyncSession, loser_id: int, winner_id: int
) -> tuple[int, list[Discard]]:
    winner_assets = set(
        await session.scalars(
            select(Implementation.tech_asset_id).where(Implementation.control_id == winner_id)
        )
    )
    rows = list(
        await session.scalars(
            select(Implementation)
            .where(Implementation.control_id == loser_id)
            .order_by(Implementation.id)
        )
    )
    asset_ids = [row.tech_asset_id for row in rows if row.tech_asset_id is not None]
    assets = {
        asset.id: asset
        for asset in (
            await session.scalars(select(TechAsset).where(TechAsset.id.in_(asset_ids)))
            if asset_ids
            else []
        )
    }
    moved = 0
    discards: list[Discard] = []
    for row in rows:
        if row.tech_asset_id in winner_assets:
            if row.tech_asset_id is None:
                discard = Discard(
                    table="implementations",
                    id=row.id,
                    detail="无绑定资产：赢家已有",
                    code="merge.discard.implementation_unbound_duplicate",
                )
            else:
                name = assets[row.tech_asset_id].name
                discard = Discard(
                    table="implementations",
                    id=row.id,
                    detail=f"资产 {name}：赢家已有",
                    code="merge.discard.implementation_duplicate",
                    params={"asset": name},
                )
            discards.append(discard)
        else:
            moved += 1
    return moved, discards


async def _plan_mappings(
    session: AsyncSession, loser_id: int, winner_id: int
) -> tuple[int, list[Discard]]:
    winner_by_item = {
        row.framework_item_id: row
        for row in await session.scalars(select(Mapping).where(Mapping.control_id == winner_id))
    }
    rows = (
        await session.execute(
            select(Mapping, FrameworkItem)
            .join(FrameworkItem, FrameworkItem.id == Mapping.framework_item_id)
            .where(Mapping.control_id == loser_id)
            .order_by(Mapping.id)
        )
    ).all()
    moved = 0
    discards: list[Discard] = []
    for mapping, item in rows:
        existing = winner_by_item.get(mapping.framework_item_id)
        if existing is None:
            moved += 1
            continue
        detail = f"{item.code}（{mapping.strength.value}）——赢家已有 {existing.strength.value}"
        params: dict[str, object] = {
            "item": item.code,
            "strength": mapping.strength.value,
            "existing": existing.strength.value,
        }
        if mapping.quote:
            # 摘录会随丢弃一起消失，必须让人在点确认之前看到它。
            detail += f"；摘录：{mapping.quote}"
            discard = Discard(
                table="mappings",
                id=mapping.id,
                detail=detail,
                code="merge.discard.mapping_duplicate_quoted",
                params={**params, "quote": mapping.quote},
            )
        else:
            discard = Discard(
                table="mappings",
                id=mapping.id,
                detail=detail,
                code="merge.discard.mapping_duplicate",
                params=params,
            )
        discards.append(discard)
    return moved, discards


async def plan_merge(session: AsyncSession, *, loser_id: int, winner_id: int) -> MergePlan:
    """按表名字母序、表内 id 升序分类。本函数不得写入。"""
    loser, winner, blockers = await _load_and_blockers(session, loser_id, winner_id)
    if blockers:
        return MergePlan(
            loser_code=loser.code if loser else "",
            winner_code=winner.code if winner else "",
            moves={},
            discards=[],
            blockers=blockers,
        )
    assert loser is not None and winner is not None

    moves: dict[str, int] = {}
    discards: list[Discard] = []

    discards.extend(await _plan_embeddings(session, loser_id))

    n, dropped = await _plan_relations(session, loser, winner)
    _add_moves(moves, "control_relations", n)
    discards.extend(dropped)

    n, dropped = await _plan_sources(session, loser_id, winner_id)
    _add_moves(moves, "control_sources", n)
    discards.extend(dropped)

    _add_moves(moves, "evidence_items", await _count_on_loser(session, EvidenceItem, loser_id))

    n, dropped = await _plan_implementations(session, loser_id, winner_id)
    _add_moves(moves, "implementations", n)
    discards.extend(dropped)

    n, dropped = await _plan_mappings(session, loser_id, winner_id)
    _add_moves(moves, "mappings", n)
    discards.extend(dropped)

    _add_moves(moves, "risk_entries", await _count_on_loser(session, RiskEntry, loser_id))

    return MergePlan(
        loser_code=loser.code,
        winner_code=winner.code,
        moves=moves,
        discards=discards,
        blockers=[],
    )
