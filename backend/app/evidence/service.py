from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.controls.models import Control
from app.environment.models import TechAsset
from app.errors import Conflict, NotFound
from app.evidence import freshness
from app.evidence.models import EvidenceItem, EvidenceStatus, EvidenceType
from app.evidence.schemas import EvidenceItemOut
from app.iam.audit import record
from app.iam.models import User


def _type_snapshot(evidence_type: EvidenceType) -> dict[str, Any]:
    return {
        "name_zh": evidence_type.name_zh,
        "name_en": evidence_type.name_en,
        "format": evidence_type.format,
        "cadence": evidence_type.cadence.value,
        "typical_source": evidence_type.typical_source,
        "description": evidence_type.description,
    }


def _item_snapshot(item: EvidenceItem) -> dict[str, Any]:
    return {
        "evidence_type_id": item.evidence_type_id,
        "control_id": item.control_id,
        "tech_asset_id": item.tech_asset_id,
        "title": item.title,
        "owner_user_id": item.owner_user_id,
        "location_hint": item.location_hint,
        "last_collected_at": item.last_collected_at.isoformat() if item.last_collected_at else None,
        "valid_until": item.valid_until.isoformat() if item.valid_until else None,
        "file_path": item.file_path,
        "status": item.status.value,
    }


def item_out(
    item: EvidenceItem,
    *,
    evidence_type_name: str | None = None,
    control_code: str | None = None,
    control_title: str | None = None,
    tech_asset_name: str | None = None,
    now: datetime | None = None,
) -> EvidenceItemOut:
    calculated = freshness.display_status(item.status, item.valid_until, now)
    return EvidenceItemOut(
        id=item.id,
        evidence_type_id=item.evidence_type_id,
        control_id=item.control_id,
        tech_asset_id=item.tech_asset_id,
        title=item.title,
        owner_user_id=item.owner_user_id,
        location_hint=item.location_hint,
        last_collected_at=item.last_collected_at,
        valid_until=item.valid_until,
        file_path=item.file_path,
        status=calculated,
        intent_status=item.status,
        display_status=calculated,
        created_at=item.created_at,
        evidence_type_name=evidence_type_name,
        control_code=control_code,
        control_title=control_title,
        tech_asset_name=tech_asset_name,
    )


async def create_type(
    session: AsyncSession,
    data: dict[str, Any],
    *,
    actor: User,
    ip: str | None = None,
) -> EvidenceType:
    evidence_type = EvidenceType(**data)
    session.add(evidence_type)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("证据类型无法保存", code="evidence_type.save_failed") from exc
    await record(
        session,
        user=actor,
        action="evidence_type.create",
        entity_type="EvidenceType",
        entity_id=evidence_type.id,
        after=_type_snapshot(evidence_type),
        ip=ip,
    )
    return evidence_type


async def update_type(
    session: AsyncSession,
    evidence_type: EvidenceType,
    data: dict[str, Any],
    *,
    actor: User,
    ip: str | None = None,
) -> EvidenceType:
    before = _type_snapshot(evidence_type)
    for field, value in data.items():
        setattr(evidence_type, field, value)
    await session.flush()
    await record(
        session,
        user=actor,
        action="evidence_type.update",
        entity_type="EvidenceType",
        entity_id=evidence_type.id,
        before=before,
        after=_type_snapshot(evidence_type),
        ip=ip,
    )
    return evidence_type


async def delete_type(
    session: AsyncSession,
    evidence_type: EvidenceType,
    *,
    actor: User,
    ip: str | None = None,
) -> None:
    before = _type_snapshot(evidence_type)
    await session.delete(evidence_type)
    await session.flush()
    await record(
        session,
        user=actor,
        action="evidence_type.delete",
        entity_type="EvidenceType",
        entity_id=evidence_type.id,
        before=before,
        ip=ip,
    )


async def list_types(session: AsyncSession) -> list[EvidenceType]:
    return list(await session.scalars(select(EvidenceType).order_by(EvidenceType.name_en)))


async def _check_item_references(
    session: AsyncSession,
    *,
    evidence_type_id: int,
    control_id: int,
    tech_asset_id: int | None,
) -> None:
    if await session.get(EvidenceType, evidence_type_id) is None:
        raise NotFound("证据类型不存在", code="evidence_type.not_found")
    if await session.get(Control, control_id) is None:
        raise NotFound("控制点不存在", code="control.not_found")
    if tech_asset_id is not None and await session.get(TechAsset, tech_asset_id) is None:
        raise NotFound("技术资产不存在", code="tech_asset.not_found")


async def create_item(
    session: AsyncSession,
    data: dict[str, Any],
    *,
    actor: User,
    ip: str | None = None,
) -> EvidenceItem:
    await _check_item_references(
        session,
        evidence_type_id=data["evidence_type_id"],
        control_id=data["control_id"],
        tech_asset_id=data.get("tech_asset_id"),
    )
    item = EvidenceItem(**data)
    session.add(item)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("证据登记不符合数据库约束", code="evidence.constraint_violation") from exc
    await record(
        session,
        user=actor,
        action="evidence_item.create",
        entity_type="EvidenceItem",
        entity_id=item.id,
        after=_item_snapshot(item),
        ip=ip,
    )
    return item


async def update_item(
    session: AsyncSession,
    item: EvidenceItem,
    data: dict[str, Any],
    *,
    actor: User,
    ip: str | None = None,
) -> EvidenceItem:
    values = {
        "evidence_type_id": data.get("evidence_type_id", item.evidence_type_id),
        "control_id": data.get("control_id", item.control_id),
        "tech_asset_id": data.get("tech_asset_id", item.tech_asset_id),
    }
    await _check_item_references(session, **values)
    before = _item_snapshot(item)
    for field, value in data.items():
        setattr(item, field, value)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("证据登记不符合数据库约束", code="evidence.constraint_violation") from exc
    await record(
        session,
        user=actor,
        action="evidence_item.update",
        entity_type="EvidenceItem",
        entity_id=item.id,
        before=before,
        after=_item_snapshot(item),
        ip=ip,
    )
    return item


async def delete_item(
    session: AsyncSession,
    item: EvidenceItem,
    *,
    actor: User,
    ip: str | None = None,
) -> None:
    before = _item_snapshot(item)
    await session.delete(item)
    await session.flush()
    await record(
        session,
        user=actor,
        action="evidence_item.delete",
        entity_type="EvidenceItem",
        entity_id=item.id,
        before=before,
        ip=ip,
    )


async def list_items(
    session: AsyncSession,
    *,
    owner_user_id: int | None = None,
    display_status: str | None = None,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    limit: int = 100,
) -> list[EvidenceItemOut]:
    needs_python_status_filter = display_status in {"valid", "expired"}
    stmt = (
        select(EvidenceItem, EvidenceType, Control, TechAsset)
        .join(EvidenceType, EvidenceType.id == EvidenceItem.evidence_type_id)
        .join(Control, Control.id == EvidenceItem.control_id)
        .outerjoin(TechAsset, TechAsset.id == EvidenceItem.tech_asset_id)
        .order_by(EvidenceItem.valid_until.asc().nullslast(), EvidenceItem.id.desc())
    )
    if owner_user_id is not None:
        stmt = stmt.where(EvidenceItem.owner_user_id == owner_user_id)
    if due_before is not None:
        stmt = stmt.where(EvidenceItem.valid_until <= due_before)
    if due_after is not None:
        stmt = stmt.where(EvidenceItem.valid_until >= due_after)
    if display_status in {item.value for item in EvidenceStatus}:
        stmt = stmt.where(EvidenceItem.status == display_status)
    if not needs_python_status_filter:
        stmt = stmt.limit(limit)
    rows = await session.execute(stmt)
    result = [
        item_out(
            item,
            evidence_type_name=evidence_type.name_en,
            control_code=control.code,
            control_title=control.title,
            tech_asset_name=asset.name if asset else None,
        )
        for item, evidence_type, control, asset in rows
    ]
    if needs_python_status_filter:
        result = [row for row in result if row.display_status == display_status]
    return result[:limit]


async def items_for_controls(
    session: AsyncSession, control_ids: Iterable[int]
) -> dict[int, list[EvidenceItemOut]]:
    ids = set(control_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(EvidenceItem, EvidenceType, Control, TechAsset)
        .join(EvidenceType, EvidenceType.id == EvidenceItem.evidence_type_id)
        .join(Control, Control.id == EvidenceItem.control_id)
        .outerjoin(TechAsset, TechAsset.id == EvidenceItem.tech_asset_id)
        .where(EvidenceItem.control_id.in_(ids))
        .order_by(EvidenceItem.control_id, EvidenceItem.id)
    )
    result = {control_id: [] for control_id in ids}
    for item, evidence_type, control, asset in rows:
        result[item.control_id].append(
            item_out(
                item,
                evidence_type_name=evidence_type.name_en,
                control_code=control.code,
                control_title=control.title,
                tech_asset_name=asset.name if asset else None,
            )
        )
    return result


async def expired_count(session: AsyncSession, *, now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    return int(
        await session.scalar(
            select(func.count())
            .select_from(EvidenceItem)
            .where(
                EvidenceItem.status == EvidenceStatus.COLLECTED,
                EvidenceItem.valid_until.is_not(None),
                EvidenceItem.valid_until < current,
            )
        )
        or 0
    )
