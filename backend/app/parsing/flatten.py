"""条款树到 Clause 行，并生成可审计的引用锚点。"""

from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.parsing.contract import ClauseNode, SourceBlock

PATH_SEPARATOR = " › "


def build_citation_label(number: str | None, heading_path: str) -> str:
    return number if number else heading_path


def _collision_keys(labels: list[str]) -> list[int]:
    """返回当前标签列表中仍相撞（被多行共享）的行下标，按行序返回。

    只依赖行序遍历，不依赖 set/dict 的迭代随机性，保证确定性。
    """
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    return [i for i, label in enumerate(labels) if counts[label] > 1]


def _nearest_unnumbered_heading(
    ancestor_chain: list[tuple[str | None, str]],
) -> str | None:
    """沿祖先链（最近祖先在前）找第一个未编号祖先的标题，没有则返回 None。"""
    for number, heading in ancestor_chain:
        if number is None:
            return heading
    return None


def _disambiguate_labels(
    rows: list[dict[str, Any]],
    ancestor_chains: list[list[tuple[str | None, str]]],
) -> None:
    """第二趟：就地消歧相撞的 citation_label。

    无撞号时立即返回、不做任何修改（no-op），保证无撞号文档逐字节一致。
    对相撞且标签来自编号的行，用其最近未编号祖先标题限定；仍相撞时按
    heading_path → order_index 的确定性阶梯兜底，最终保证全局唯一且顺序稳定。
    """
    labels = [row["citation_label"] for row in rows]
    collisions = _collision_keys(labels)
    if not collisions:
        return

    # 第一级限定：仅处理标签来自编号的相撞行，用其最近未编号祖先标题限定。
    for key in collisions:
        row = rows[key]
        if row["number"] is None:
            continue
        ancestor_heading = _nearest_unnumbered_heading(ancestor_chains[key])
        if ancestor_heading is None:
            continue
        labels[key] = f"{ancestor_heading}{PATH_SEPARATOR}{row['number']}"

    # 第二级兜底 (a)：对限定后仍相撞的行改用完整 heading_path 作为限定基。
    for key in _collision_keys(labels):
        row = rows[key]
        if row["number"] is None:
            labels[key] = row["heading_path"]
        else:
            labels[key] = f"{row['heading_path']}{PATH_SEPARATOR}{row['number']}"

    # 第三级兜底 (b)：仍相撞则追加唯一且稳定的 order_index，保证绝对唯一。
    for key in _collision_keys(labels):
        row = rows[key]
        labels[key] = f"{labels[key]}{PATH_SEPARATOR}#{row['order_index']}"

    for key, label in enumerate(labels):
        rows[key]["citation_label"] = label


def flatten(
    nodes: list[ClauseNode], *, document_id: int, language: str = "en"
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # 与 rows 下标对齐的并行列表：每项是该行从直接父到根的 (number, heading) 链
    # （最近祖先在前），供第二趟就近查找未编号祖先，无需再遍历树。
    ancestor_chains: list[list[tuple[str | None, str]]] = []

    def walk(
        node: ClauseNode,
        ancestors: list[str],
        parent_key: int | None,
        ancestor_chain: list[tuple[str | None, str]],
    ) -> None:
        path = [*ancestors, node.heading]
        heading_path = PATH_SEPARATOR.join(path)
        key = len(rows)
        rows.append(
            {
                "_parent_key": parent_key,
                "_source_keys": node.source_keys,
                "document_id": document_id,
                "number": node.number,
                "heading": node.heading,
                "heading_path": heading_path,
                "citation_label": build_citation_label(node.number, heading_path),
                "text": node.text,
                "order_index": key,
                "level": node.level,
                "page_ref": node.page_ref,
                "kind": node.kind,
                "language": language,
            }
        )
        # key == len(ancestor_chains)，与 rows 严格对齐。
        ancestor_chains.append(ancestor_chain)
        # 子节点的祖先链 = 当前节点在前，其后接本节点的祖先链。
        child_chain = [(node.number, node.heading), *ancestor_chain]
        for child in node.children:
            walk(child, path, key, child_chain)

    for root in nodes:
        walk(root, [], None, [])
    _disambiguate_labels(rows, ancestor_chains)
    return rows


async def persist(
    session: AsyncSession,
    nodes: list[ClauseNode],
    *,
    document_id: int,
    language: str = "en",
    source_blocks: list[SourceBlock] | None = None,
    source_origin: str = "unknown",
) -> int:
    from app.clauses.models import ClauseBlockSource
    from app.parsing.sources import persist_blocks

    block_ids = await persist_blocks(
        session, document_id, source_blocks, origin=source_origin,
    ) if source_blocks is not None else {}
    await session.execute(delete(Clause).where(Clause.document_id == document_id))

    rows = flatten(nodes, document_id=document_id, language=language)
    id_by_key: dict[int, int] = {}
    for key, row in enumerate(rows):
        parent_key = row.pop("_parent_key")
        source_keys = row.pop("_source_keys")
        clause = Clause(
            **row,
            parent_id=id_by_key[parent_key] if parent_key is not None else None,
        )
        session.add(clause)
        await session.flush()
        id_by_key[key] = clause.id
        if source_blocks is not None:
            for source_key in set(source_keys):
                # 解析器声称了一个不存在的出处时，整笔事务失败，不能静默漏关联。
                session.add(ClauseBlockSource(clause_id=clause.id, block_id=block_ids[source_key]))

    await session.flush()

    return len(rows)
