"""块级出处读取和编辑传递，不把关联范围冒充字符级定位。"""

from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause, ClauseBlockSource, OriginalBlock
from app.errors import NotFound
from app.ingest.models import Document


async def copy_sources(session: AsyncSession, *, from_id: int, to_id: int) -> None:
    statement = insert(ClauseBlockSource).from_select(
        ["clause_id", "block_id"],
        select(literal(to_id), ClauseBlockSource.block_id)
        .where(ClauseBlockSource.clause_id == from_id),
    ).on_conflict_do_nothing()
    await session.execute(statement)


async def source_page(
    session: AsyncSession, document_id: int, *, clause_id: int | None = None,
    offset: int = 0, limit: int = 100, unassigned_only: bool = False,
) -> dict:
    document = await session.get(Document, document_id)
    if document is None:
        raise NotFound("文档不存在", code="document.not_found")
    active_links = select(ClauseBlockSource.block_id).join(
        Clause, Clause.id == ClauseBlockSource.clause_id,
    ).where(Clause.document_id == document_id, Clause.status != "merged")
    filters = [OriginalBlock.document_id == document_id]
    if clause_id is None:
        filters.append(OriginalBlock.snapshot == document.source_snapshot)
    else:
        filters.append(OriginalBlock.id.in_(select(ClauseBlockSource.block_id)
                       .where(ClauseBlockSource.clause_id == clause_id)))
    total = await session.scalar(select(func.count()).select_from(OriginalBlock).where(*filters))
    unassigned = await session.scalar(select(func.count()).select_from(OriginalBlock).where(
        *filters, OriginalBlock.id.notin_(active_links),
    ))
    if unassigned_only:
        filters.append(OriginalBlock.id.notin_(active_links))
    blocks = list(await session.scalars(select(OriginalBlock).where(*filters)
                                       .order_by(OriginalBlock.order_index, OriginalBlock.id)
                                       .offset(offset).limit(limit)))
    links: dict[int, list[int]] = {block.id: [] for block in blocks}
    if links:
        rows = await session.execute(select(ClauseBlockSource.block_id, ClauseBlockSource.clause_id)
                                     .join(Clause, Clause.id == ClauseBlockSource.clause_id)
                                     .where(ClauseBlockSource.block_id.in_(links),
                                            Clause.document_id == document_id,
                                            Clause.status != "merged")
                                     .order_by(ClauseBlockSource.clause_id))
        for block_id, linked_clause_id in rows:
            links[block_id].append(linked_clause_id)
    return {
        "available": document.source_snapshot is not None,
        "total": total, "unassigned": unassigned,
        "offset": offset, "limit": limit,
        "has_more": offset + len(blocks) < (unassigned if unassigned_only else total),
        "blocks": [{"id": block.id, "text": block.text, "kind": block.kind,
                    "origin": block.origin, "locator": block.locator,
                    "clause_ids": links[block.id]} for block in blocks],
    }
