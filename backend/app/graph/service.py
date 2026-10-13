from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.controls.models import (
    Control,
    ControlRelation,
    ControlSource,
    RelationType,
    SourceRelation,
    active_controls,
)
from app.errors import BadRequest
from app.frameworks.models import FrameworkItem, Mapping
from app.graph.schemas import GraphEdge, GraphGroup, GraphNode, GraphOut, GraphStats
from app.ingest.models import Document
from app.review.models import Proposal, ProposalKind, ProposalStatus

# 全景模式的硬上限。超了按 code 升序截断——随机截断的全景图
# 截图汇报出去两次不一样，比截断本身更糟。
MAX_NODES = 600


def control_key(control_id: int) -> str:
    return f"control:{control_id}"


def parse_focus(focus: str) -> tuple[str, int]:
    """解析 focus 参数：`control:57` / `document:3` / `item:1172` → ("control", 57)。"""
    kind, _, raw = focus.partition(":")
    if kind not in {"control", "document", "item"} or not raw.isdigit():
        raise BadRequest("focus 需形如 control:57 / document:3 / item:1172", code="graph.focus_malformed")
    return kind, int(raw)


def _neighbourhood(seeds: set[str], edges: list[GraphEdge], hops: int) -> set[str]:
    """在合并后的边集上做 BFS。

    因此打开待确认边之后，原本 2 跳可达的点可能变 1 跳——这是对的，
    但也正因如此 stats 必须把两类边分开计数。
    """
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, set()).add(edge.target)
        adjacency.setdefault(edge.target, set()).add(edge.source)

    reached = set(seeds)
    frontier = set(seeds)
    for _ in range(hops):
        nxt: set[str] = set()
        for key in frontier:
            nxt |= adjacency.get(key, set()) - reached
        if not nxt:
            break
        reached |= nxt
        frontier = nxt
    return reached


def _pair_key(source: str, target: str, kind: str) -> tuple[str, str, str]:
    """duplicates 无方向，正反两条是同一条；depends_on 有方向，不可混。"""
    if kind == RelationType.DUPLICATES.value:
        first, second = sorted((source, target))
        return first, second, kind
    return source, target, kind


async def _pending_relation_edges(
    session: AsyncSession, keys: set[str], types: set[RelationType] | None
) -> list[GraphEdge]:
    rows = (
        await session.execute(
            select(Proposal)
            .where(
                Proposal.kind == ProposalKind.RELATION,
                Proposal.status == ProposalStatus.PENDING,
            )
            .order_by(Proposal.id)
        )
    ).scalars().all()

    edges: list[GraphEdge] = []
    for row in rows:
        payload = row.payload or {}
        source = control_key(payload.get("from_control_id", 0))
        target = control_key(payload.get("to_control_id", 0))
        kind = payload.get("relation_type", "")
        if source not in keys or target not in keys:
            continue
        if types is not None and kind not in {t.value for t in types}:
            continue
        edges.append(
            GraphEdge(
                key=f"proposal:{row.id}",
                source=source,
                target=target,
                kind=kind,
                status="pending",
                confidence=payload.get("confidence", row.confidence),
                rationale=payload.get("rationale", ""),
                proposal_id=row.id,
            )
        )
    return edges


async def _conflict_edges(
    session: AsyncSession, keys: set[str], include_pending: bool
) -> list[GraphEdge]:
    """冲突边不落库，从 PolicyConflict 与待确认提案现算。

    同一对控制点的多处冲突聚成一条边并带计数；两条冲突条款同属一个控制点时
    是自环，跳过——当前语料里没有一个控制点跨文件，但派生逻辑不得因此崩。
    """
    from app.conflicts.models import PolicyConflict, normalise_pair

    owners: dict[int, set[int]] = {}
    rows = await session.execute(
        select(ControlSource.clause_id, ControlSource.control_id)
    )
    for clause_id, control_id in rows:
        owners.setdefault(clause_id, set()).add(control_id)

    # (source_key, target_key, status) → 冲突处数
    tally: dict[tuple[str, str, str], int] = {}

    def add(clause_a: int, clause_b: int, status: str) -> None:
        for left in sorted(owners.get(clause_a, set())):
            for right in sorted(owners.get(clause_b, set())):
                if left == right:
                    continue  # 自环
                low, high = normalise_pair(left, right)
                pair = (control_key(low), control_key(high), status)
                if pair[0] in keys and pair[1] in keys:
                    tally[pair] = tally.get(pair, 0) + 1

    for row in (await session.execute(select(PolicyConflict).order_by(PolicyConflict.id))).scalars():
        add(row.clause_a_id, row.clause_b_id, "confirmed")

    if include_pending:
        proposals = (
            await session.execute(
                select(Proposal)
                .where(
                    Proposal.kind == ProposalKind.CONFLICT,
                    Proposal.status == ProposalStatus.PENDING,
                )
                .order_by(Proposal.id)
            )
        ).scalars().all()
        confirmed_pairs = {(s, t) for s, t, status in tally if status == "confirmed"}
        for row in proposals:
            payload = row.payload or {}
            a, b = payload.get("clause_a_id"), payload.get("clause_b_id")
            if not isinstance(a, int) or not isinstance(b, int) or a == b:
                continue
            before = set(tally)
            add(a, b, "pending")
            # 已确认的那对不再重复画一条虚线。
            for key in set(tally) - before:
                if (key[0], key[1]) in confirmed_pairs:
                    tally.pop(key, None)

    return [
        GraphEdge(
            key=f"conflict:{source}:{target}:{status}",
            source=source,
            target=target,
            kind="conflicts_with",
            status=status,
            rationale="",
            conflict_count=count,
        )
        # 行序定死，两次请求的边顺序必须一致。
        for (source, target, status), count in sorted(tally.items())
    ]


async def _relation_seeds(session: AsyncSession, kind: str, target_id: int) -> set[str]:
    if kind == "control":
        return {control_key(target_id)}
    if kind == "document":
        rows = (
            await session.execute(
                select(ControlSource.control_id)
                .join(Clause, Clause.id == ControlSource.clause_id)
                .where(Clause.document_id == target_id)
            )
        ).scalars().all()
        return {control_key(control_id) for control_id in rows}
    raise BadRequest("关系图的 focus 只能是 control: 或 document:", code="graph.focus_kind_unsupported")


async def _document_groups(
    session: AsyncSession,
) -> tuple[dict[int, str], dict[int, list[str]], dict[str, str]]:
    """控制点 → (主文件 key, 其余文件 key 列表)，外加 key → 文件名。

    主文件取 relation='defines' 的那份；多条 defines 或一条都没有时，
    取 clause_id 最小的那条——必须确定，否则同一份数据两次分组会不一样。
    """
    rows = (
        await session.execute(
            select(
                ControlSource.control_id,
                ControlSource.clause_id,
                ControlSource.relation,
                Clause.document_id,
                Document.title,
            )
            .join(Clause, Clause.id == ControlSource.clause_id)
            .join(Document, Document.id == Clause.document_id)
            .order_by(ControlSource.control_id, ControlSource.clause_id)
        )
    ).all()

    labels: dict[str, str] = {}
    per_control: dict[int, list[tuple[int, int, str]]] = {}
    for control_id, clause_id, relation, document_id, title in rows:
        key = f"document:{document_id}"
        labels[key] = title
        weight = 0 if relation == SourceRelation.DEFINES else 1
        per_control.setdefault(control_id, []).append((weight, clause_id, key))

    primary: dict[int, str] = {}
    extra: dict[int, list[str]] = {}
    for control_id, entries in per_control.items():
        entries.sort()  # 先 defines，同权重再比 clause_id——排序即确定性
        ordered: list[str] = []
        for _weight, _clause_id, key in entries:
            if key not in ordered:
                ordered.append(key)
        primary[control_id] = ordered[0]
        extra[control_id] = ordered[1:]
    return primary, extra, labels


async def _control_functions(session: AsyncSession) -> tuple[dict[int, list[str]], dict[int, set[int]]]:
    """控制点 → CSF Function 代码列表，以及控制点 → 它映射到的框架 id 集合。"""
    items = (
        await session.execute(select(FrameworkItem.id, FrameworkItem.parent_id, FrameworkItem.code))
    ).all()
    parent = {item_id: parent_id for item_id, parent_id, _code in items}
    code = {item_id: item_code for item_id, _parent_id, item_code in items}

    def root_code(item_id: int) -> str:
        current = item_id
        while parent.get(current) is not None:
            current = parent[current]
        return code.get(current, "")

    rows = (
        await session.execute(
            select(Mapping.control_id, Mapping.framework_item_id, FrameworkItem.framework_id)
            .join(FrameworkItem, FrameworkItem.id == Mapping.framework_item_id)
            .order_by(Mapping.control_id, Mapping.id)
        )
    ).all()

    functions: dict[int, list[str]] = {}
    frameworks: dict[int, set[int]] = {}
    for control_id, item_id, framework_id in rows:
        bucket = functions.setdefault(control_id, [])
        function_code = root_code(item_id)
        if function_code and function_code not in bucket:
            bucket.append(function_code)
        frameworks.setdefault(control_id, set()).add(framework_id)
    return functions, frameworks


async def relation_graph(
    session: AsyncSession,
    *,
    focus: str | None = None,
    hops: int = 1,
    include_pending: bool = False,
    types: set[RelationType] | None = None,
    framework_id: int | None = None,
) -> GraphOut:
    controls = (await session.execute(active_controls().order_by(Control.code))).scalars().all()
    primary, extra, labels = await _document_groups(session)
    functions, frameworks = await _control_functions(session)

    if framework_id is not None:
        controls = [c for c in controls if framework_id in frameworks.get(c.id, set())]

    truncated = False
    if focus is None and len(controls) > MAX_NODES:
        controls = controls[:MAX_NODES]  # 已按 code 升序
        truncated = True

    nodes = [
        GraphNode(
            key=control_key(c.id),
            kind="control",
            code=c.code,
            title=c.title,
            group=primary.get(c.id),
            group_extra=extra.get(c.id, []),
            functions=functions.get(c.id, []),
        )
        for c in controls
    ]
    keys = {node.key for node in nodes}

    stmt = select(ControlRelation).order_by(ControlRelation.id)
    if types is not None:
        stmt = stmt.where(ControlRelation.relation_type.in_(types))
    relations = (await session.execute(stmt)).scalars().all()
    edges = [
        GraphEdge(
            key=f"relation:{row.id}",
            source=control_key(row.from_control_id),
            target=control_key(row.to_control_id),
            kind=row.relation_type.value,
            status="confirmed",
            confidence=row.confidence,
            rationale=row.rationale,
        )
        for row in relations
        if control_key(row.from_control_id) in keys and control_key(row.to_control_id) in keys
    ]

    if include_pending:
        seen = {_pair_key(e.source, e.target, e.kind) for e in edges}
        for edge in await _pending_relation_edges(session, keys, types):
            pair = _pair_key(edge.source, edge.target, edge.kind)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(edge)

    # 冲突边只在没有按类型筛选、或明确要 conflicts_with 时才画。
    if types is None or RelationType.CONFLICTS_WITH in types:
        edges.extend(await _conflict_edges(session, keys, include_pending))

    if focus is not None:
        kind, target_id = parse_focus(focus)
        seeds = await _relation_seeds(session, kind, target_id)
        reached = _neighbourhood(seeds, edges, hops)
        nodes = [node for node in nodes if node.key in reached]
        keys = {node.key for node in nodes}
        edges = [e for e in edges if e.source in keys and e.target in keys]

    pending_count: dict[str, int] = {}
    for edge in edges:
        if edge.status != "pending":
            continue
        pending_count[edge.source] = pending_count.get(edge.source, 0) + 1
        pending_count[edge.target] = pending_count.get(edge.target, 0) + 1
    for node in nodes:
        node.pending_edges = pending_count.get(node.key, 0)

    used = {node.group for node in nodes if node.group} | {
        key for node in nodes for key in node.group_extra
    }
    groups = [
        GraphGroup(key=key, kind="document", label=labels.get(key, key)) for key in sorted(used)
    ]

    return GraphOut(
        nodes=nodes,
        edges=edges,
        groups=groups,
        stats=GraphStats(
            nodes=len(nodes),
            edges=len(edges),
            pending_edges=sum(1 for e in edges if e.status == "pending"),
            truncated=truncated,
        ),
    )


def item_key(item_id: int) -> str:
    return f"item:{item_id}"


async def mapping_graph(
    session: AsyncSession,
    *,
    framework_id: int,
    focus: str | None = None,
    hops: int = 1,
    include_pending: bool = False,
    only_gaps: bool = False,
) -> GraphOut:
    items = (
        await session.execute(
            select(FrameworkItem)
            .where(FrameworkItem.framework_id == framework_id)
            .order_by(FrameworkItem.order_index, FrameworkItem.code)
        )
    ).scalars().all()
    item_ids = {item.id for item in items}

    mappings = (
        await session.execute(
            select(Mapping)
            .join(FrameworkItem, FrameworkItem.id == Mapping.framework_item_id)
            .where(FrameworkItem.framework_id == framework_id)
            .order_by(Mapping.id)
        )
    ).scalars().all()

    covered = {row.framework_item_id for row in mappings}
    parents = {item.parent_id for item in items if item.parent_id is not None}
    control_ids = {row.control_id for row in mappings}

    pending_rows: list[Proposal] = []
    if include_pending:
        pending_rows = (
            await session.execute(
                select(Proposal)
                .where(
                    Proposal.kind == ProposalKind.MAPPING,
                    Proposal.status == ProposalStatus.PENDING,
                )
                .order_by(Proposal.id)
            )
        ).scalars().all()
        pending_rows = [
            row for row in pending_rows if (row.payload or {}).get("framework_item_id") in item_ids
        ]
        control_ids |= {(row.payload or {}).get("control_id", 0) for row in pending_rows}

    controls: list[Control] = []
    if control_ids:
        controls = (
            await session.execute(
                select(Control).where(Control.id.in_(control_ids)).order_by(Control.code)
            )
        ).scalars().all()

    item_nodes = [
        GraphNode(
            key=item_key(item.id),
            kind="framework_item",
            code=item.code,
            title=item.title,
            # 差距只看已确认的连线：一条待确认的提案不代表这项已被覆盖。
            # 有子项的容器不算差距——覆盖落在叶子上。
            is_gap=item.id not in covered and item.id not in parents,
        )
        for item in items
    ]
    control_nodes = [
        GraphNode(key=control_key(c.id), kind="control", code=c.code, title=c.title)
        for c in controls
    ]

    if only_gaps:
        return GraphOut(
            nodes=[node for node in item_nodes if node.is_gap],
            edges=[],
            groups=[],
            stats=GraphStats(
                nodes=sum(1 for node in item_nodes if node.is_gap),
                edges=0,
                pending_edges=0,
                truncated=False,
            ),
        )

    nodes = control_nodes + item_nodes
    keys = {node.key for node in nodes}

    edges = [
        GraphEdge(
            key=f"mapping:{row.id}",
            source=control_key(row.control_id),
            target=item_key(row.framework_item_id),
            kind=row.strength.value,
            status="confirmed",
            confidence=row.confidence,
            rationale=row.rationale,
        )
        for row in mappings
        if control_key(row.control_id) in keys and item_key(row.framework_item_id) in keys
    ]
    seen = {(e.source, e.target) for e in edges}
    for row in pending_rows:
        payload = row.payload or {}
        source = control_key(payload.get("control_id", 0))
        target = item_key(payload.get("framework_item_id", 0))
        if source not in keys or target not in keys or (source, target) in seen:
            continue
        seen.add((source, target))
        edges.append(
            GraphEdge(
                key=f"proposal:{row.id}",
                source=source,
                target=target,
                kind=payload.get("strength", ""),
                status="pending",
                confidence=payload.get("confidence", row.confidence),
                rationale=payload.get("rationale", ""),
                proposal_id=row.id,
            )
        )

    if focus is not None:
        kind, target_id = parse_focus(focus)
        if kind == "control":
            seeds = {control_key(target_id)}
        elif kind == "item":
            seeds = {item_key(target_id)}
        else:
            rows = (
                await session.execute(
                    select(ControlSource.control_id)
                    .join(Clause, Clause.id == ControlSource.clause_id)
                    .where(Clause.document_id == target_id)
                )
            ).scalars().all()
            seeds = {control_key(control_id) for control_id in rows}
        reached = _neighbourhood(seeds, edges, hops) | seeds
        nodes = [node for node in nodes if node.key in reached]
        keys = {node.key for node in nodes}
        edges = [e for e in edges if e.source in keys and e.target in keys]

    pending_count: dict[str, int] = {}
    for edge in edges:
        if edge.status != "pending":
            continue
        pending_count[edge.source] = pending_count.get(edge.source, 0) + 1
        pending_count[edge.target] = pending_count.get(edge.target, 0) + 1
    for node in nodes:
        node.pending_edges = pending_count.get(node.key, 0)

    return GraphOut(
        nodes=nodes,
        edges=edges,
        groups=[],
        stats=GraphStats(
            nodes=len(nodes),
            edges=len(edges),
            pending_edges=sum(1 for e in edges if e.status == "pending"),
            truncated=False,
        ),
    )
