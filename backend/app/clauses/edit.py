"""条款拆/合。不删行，每次改动写审计。"""

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.clauses.sources import copy_sources
from app.controls.models import ControlSource
from app.errors import AppError, NotFound
from app.iam.audit import record
from app.iam.models import User
from app.indexing.service import rebuild_chunks
from app.ingest.models import DocStatus, Document
from app.parsing.flatten import PATH_SEPARATOR, build_citation_label


async def _lock_editable(session: AsyncSession, document_id: int) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(734005, :doc)"), {"doc": document_id}
    )
    # 已被替换的旧版是历史：变更影响分析拿它的条款与新版比对，旧控制点的出处也
    # 指着它们。拆、合等于事后改写历史——影响报告与出处原文都会跟着变。
    status = await session.scalar(select(Document.status).where(Document.id == document_id))
    if status is DocStatus.SUPERSEDED:
        raise AppError("这份文档已被新版替换，条款不能再拆分或合并", code="clause.document_not_editable")


def _heading_path_for_sibling(sibling: Clause, heading: str) -> str:
    parent = PATH_SEPARATOR.join(sibling.heading_path.split(PATH_SEPARATOR)[:-1])
    return PATH_SEPARATOR.join([part for part in (parent, heading) if part])


async def split_clause(
    session: AsyncSession, clause_id: int, *, at: int, actor: User
) -> Clause:
    clause = await session.get(Clause, clause_id)
    if clause is None or clause.status == "merged":
        raise NotFound("条款不存在", code="clause.not_found")
    body = clause.text or ""
    if at <= 0 or at >= len(body):
        raise AppError("切开位置必须落在正文中间", code="clause.split_offset_invalid")
    await _lock_editable(session, clause.document_id)

    left, right = body[:at].rstrip(), body[at:].lstrip()
    if not left or not right:
        raise AppError("切开位置必须落在正文中间", code="clause.split_offset_invalid")

    before = {"text": clause.text}
    clause.text = left
    first_line = right.split("\n", 1)[0].strip()
    heading = first_line[:80] if first_line else f"{clause.heading} (continued)"
    heading_path = _heading_path_for_sibling(clause, heading)

    await session.execute(
        update(Clause)
        .where(Clause.document_id == clause.document_id, Clause.order_index > clause.order_index)
        .values(order_index=Clause.order_index + 1)
    )
    created = Clause(
        document_id=clause.document_id,
        parent_id=clause.parent_id,
        number=None,
        heading=heading,
        heading_path=heading_path,
        citation_label=build_citation_label(None, heading_path),
        text=right,
        order_index=clause.order_index + 1,
        level=clause.level,
        page_ref=clause.page_ref,
        kind=clause.kind,
        language=clause.language,
        status="active",
    )
    session.add(created)
    await session.flush()
    await copy_sources(session, from_id=clause.id, to_id=created.id)
    await rebuild_chunks(session, document_id=clause.document_id)
    await record(
        session,
        user=actor,
        action="clause.split",
        entity_type="Clause",
        entity_id=clause.id,
        before=before,
        after={"text": clause.text, "created_id": created.id},
    )
    return created


async def merge_clauses(
    session: AsyncSession, *, winner_id: int, loser_id: int, actor: User
) -> Clause:
    if winner_id == loser_id:
        raise AppError("不能与自己合并", code="clause.merge_self")
    winner = await session.get(Clause, winner_id)
    loser = await session.get(Clause, loser_id)
    if winner is None or loser is None:
        raise NotFound("条款不存在", code="clause.not_found")
    if winner.document_id != loser.document_id:
        raise AppError("只能合并同一份文档里的条款", code="clause.merge_cross_document")
    await _lock_editable(session, winner.document_id)
    # 锁之前读到的状态可能已过时：两个方向相反的合并同时到达，都会看到对方还是
    # active，结果两条都被标成 merged、正文从树上一起消失。拿到锁再重读一遍。
    await session.refresh(winner)
    await session.refresh(loser)
    if "merged" in {winner.status, loser.status}:
        raise NotFound("条款不存在", code="clause.not_found")
    # 败者的子条款要挂到胜者名下；胜者若是败者的后代，就会成为自己或自己祖先的
    # 父节点，树里出现环，从根出发一个节点都走不到——整份文档看起来没有条款。
    ancestor_id = winner.parent_id
    while ancestor_id is not None:
        if ancestor_id == loser.id:
            raise AppError("不能合并进自己的下级条款", code="clause.merge_into_descendant")
        ancestor_id = await session.scalar(select(Clause.parent_id).where(Clause.id == ancestor_id))

    winner.text = f"{winner.text.rstrip()}\n\n{loser.text.lstrip()}".strip()
    await copy_sources(session, from_id=loser.id, to_id=winner.id)
    children = list(await session.scalars(select(Clause).where(Clause.parent_id == loser.id)))
    for child in children:
        child.parent_id = winner.id

    occupied = set(
        await session.scalars(
            select(ControlSource.control_id).where(ControlSource.clause_id == winner.id)
        )
    )
    sources = list(await session.scalars(select(ControlSource).where(ControlSource.clause_id == loser.id)))
    for source in sources:
        if source.control_id in occupied:
            continue
        source.clause_id = winner.id
        occupied.add(source.control_id)

    loser.status = "merged"
    loser.merged_into_id = winner.id
    await session.flush()
    await rebuild_chunks(session, document_id=winner.document_id)
    await record(
        session,
        user=actor,
        action="clause.merge",
        entity_type="Clause",
        entity_id=loser.id,
        before={"text": loser.text, "heading": loser.heading},
        after={"merged_into": winner.id},
    )
    return winner
