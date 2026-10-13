from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.controls.models import Control, ControlRelation, ControlSource, active_controls
from app.controls.schemas import FrameworkMappingOut, RelationOut, SourceOut
from app.frameworks.models import Framework, FrameworkItem, Mapping
from app.ingest.models import Document


async def search(session: AsyncSession, *, q: str | None, limit: int) -> list[Control]:
    stmt = active_controls().order_by(Control.code).limit(limit)
    if q:
        stmt = stmt.where(
            or_(
                Control.title.icontains(q, autoescape=True),
                Control.statement.icontains(q, autoescape=True),
                Control.code.icontains(q, autoescape=True),
            )
        )
    return list(await session.scalars(stmt))


async def sources(session: AsyncSession, control_id: int) -> list[SourceOut]:
    rows = await session.execute(
        select(ControlSource, Clause, Document)
        .join(Clause, Clause.id == ControlSource.clause_id)
        .join(Document, Document.id == Clause.document_id)
        .where(ControlSource.control_id == control_id)
        .order_by(Document.id, Clause.order_index)
    )
    return [
        SourceOut(
            clause_id=clause.id,
            document_id=document.id,
            document_title=document.title,
            citation_label=clause.citation_label,
            heading_path=clause.heading_path,
            relation=source.relation,
        )
        for source, clause, document in rows
    ]


async def relations(session: AsyncSession, control_id: int) -> list[RelationOut]:
    rows = await session.scalars(
        select(ControlRelation)
        .where(
            or_(
                ControlRelation.from_control_id == control_id,
                ControlRelation.to_control_id == control_id,
            )
        )
        .order_by(ControlRelation.id)
    )
    rows = list(rows)
    ids = {row.from_control_id for row in rows} | {row.to_control_id for row in rows}
    codes = dict((await session.execute(select(Control.id, Control.code).where(Control.id.in_(ids)))).all()) if ids else {}
    return [
        RelationOut(
            from_control_id=row.from_control_id,
            to_control_id=row.to_control_id,
            from_control_code=codes.get(row.from_control_id),
            to_control_code=codes.get(row.to_control_id),
            relation_type=row.relation_type,
            rationale=row.rationale,
        )
        for row in rows
    ]


async def mappings(session: AsyncSession, control_id: int) -> list[FrameworkMappingOut]:
    rows = await session.execute(
        select(Mapping, FrameworkItem, Framework)
        .join(FrameworkItem, FrameworkItem.id == Mapping.framework_item_id)
        .join(Framework, Framework.id == FrameworkItem.framework_id)
        .where(Mapping.control_id == control_id)
        .order_by(Framework.key, FrameworkItem.order_index, Mapping.id)
    )
    return [
        FrameworkMappingOut(
            framework_id=framework.id,
            framework_key=framework.key,
            framework_name=framework.name_zh,
            framework_name_en=framework.name_en,
            framework_item_id=item.id,
            code=item.code,
            title=item.title,
            strength=mapping.strength,
            rationale=mapping.rationale,
            quote=mapping.quote,
            confidence=mapping.confidence,
        )
        for mapping, item, framework in rows
    ]
