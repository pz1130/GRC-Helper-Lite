from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.controls.models import Control
from app.environment.models import Implementation, ImplementationStatus, TechAsset
from app.environment.schemas import ImplementationOut
from app.errors import Conflict
from app.iam.audit import record
from app.iam.models import User


def _asset_snapshot(asset: TechAsset) -> dict[str, Any]:
    return {
        "name": asset.name,
        "category": asset.category.value,
        "vendor": asset.vendor,
        "environment": asset.environment.value,
        "owner_user_id": asset.owner_user_id,
        "scope_note": asset.scope_note,
        "status": asset.status.value,
    }


def _implementation_snapshot(implementation: Implementation) -> dict[str, Any]:
    return {
        "control_id": implementation.control_id,
        "tech_asset_id": implementation.tech_asset_id,
        "description": implementation.description,
        "how_enforced": implementation.how_enforced.value,
        "status": implementation.status.value,
        "na_justification": implementation.na_justification,
        "owner_user_id": implementation.owner_user_id,
        "last_verified_at": (
            implementation.last_verified_at.isoformat()
            if implementation.last_verified_at is not None
            else None
        ),
    }


async def create_asset(
    session: AsyncSession,
    data: dict[str, Any],
    *,
    actor: User,
    ip: str | None = None,
) -> TechAsset:
    asset = TechAsset(**data)
    session.add(asset)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("技术资产名称已存在，不能重复登记", code="tech_asset.name_exists") from exc
    await record(
        session,
        user=actor,
        action="tech_asset.create",
        entity_type="TechAsset",
        entity_id=asset.id,
        after=_asset_snapshot(asset),
        ip=ip,
    )
    return asset


async def update_asset(
    session: AsyncSession,
    asset: TechAsset,
    data: dict[str, Any],
    *,
    actor: User,
    ip: str | None = None,
) -> TechAsset:
    before = _asset_snapshot(asset)
    for field, value in data.items():
        setattr(asset, field, value)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("技术资产名称已存在，不能重复登记", code="tech_asset.name_exists") from exc
    await record(
        session,
        user=actor,
        action="tech_asset.update",
        entity_type="TechAsset",
        entity_id=asset.id,
        before=before,
        after=_asset_snapshot(asset),
        ip=ip,
    )
    return asset


async def delete_asset(
    session: AsyncSession,
    asset: TechAsset,
    *,
    actor: User,
    ip: str | None = None,
) -> None:
    before = _asset_snapshot(asset)
    await session.delete(asset)
    await session.flush()
    await record(
        session,
        user=actor,
        action="tech_asset.delete",
        entity_type="TechAsset",
        entity_id=asset.id,
        before=before,
        ip=ip,
    )


async def list_assets(session: AsyncSession) -> list[TechAsset]:
    return list(await session.scalars(select(TechAsset).order_by(TechAsset.name, TechAsset.id)))


async def _check_references(
    session: AsyncSession, *, control_id: int, tech_asset_id: int | None
) -> None:
    if await session.get(Control, control_id) is None:
        from app.errors import NotFound

        raise NotFound("控制点不存在", code="control.not_found")
    if tech_asset_id is not None and await session.get(TechAsset, tech_asset_id) is None:
        from app.errors import NotFound

        raise NotFound("技术资产不存在", code="tech_asset.not_found")


async def create_implementation(
    session: AsyncSession,
    data: dict[str, Any],
    *,
    actor: User,
    ip: str | None = None,
) -> Implementation:
    await _check_references(
        session, control_id=data["control_id"], tech_asset_id=data.get("tech_asset_id")
    )
    implementation = Implementation(**data)
    session.add(implementation)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("该控制点与技术资产（或流程实现）已登记", code="tech_asset.control_already_linked") from exc
    await record(
        session,
        user=actor,
        action="implementation.create",
        entity_type="Implementation",
        entity_id=implementation.id,
        after=_implementation_snapshot(implementation),
        ip=ip,
    )
    return implementation


async def _protect_rcsa_reference(session: AsyncSession, implementation: Implementation) -> None:
    from app.rcsa.models import RcsaItem

    await session.refresh(implementation, with_for_update=True)
    if await session.scalar(select(RcsaItem.id).where(RcsaItem.implementation_id == implementation.id).limit(1)):
        raise Conflict("实施记录已被 RCSA 引用，不能删除或更换系统", code="rcsa.implementation_in_use")


async def update_implementation(
    session: AsyncSession,
    implementation: Implementation,
    data: dict[str, Any],
    *,
    actor: User,
    ip: str | None = None,
) -> Implementation:
    if "tech_asset_id" in data:
        if data["tech_asset_id"] != implementation.tech_asset_id:
            await _protect_rcsa_reference(session, implementation)
        await _check_references(
            session,
            control_id=implementation.control_id,
            tech_asset_id=data["tech_asset_id"],
        )
    before = _implementation_snapshot(implementation)
    for field, value in data.items():
        setattr(implementation, field, value)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("该控制点与技术资产（或流程实现）已登记", code="tech_asset.control_already_linked") from exc
    await record(
        session,
        user=actor,
        action="implementation.update",
        entity_type="Implementation",
        entity_id=implementation.id,
        before=before,
        after=_implementation_snapshot(implementation),
        ip=ip,
    )
    return implementation


async def delete_implementation(
    session: AsyncSession,
    implementation: Implementation,
    *,
    actor: User,
    ip: str | None = None,
) -> None:
    await _protect_rcsa_reference(session, implementation)
    before = _implementation_snapshot(implementation)
    await session.delete(implementation)
    await session.flush()
    await record(
        session,
        user=actor,
        action="implementation.delete",
        entity_type="Implementation",
        entity_id=implementation.id,
        before=before,
        ip=ip,
    )


async def list_implementations(
    session: AsyncSession,
    *,
    control_id: int | None = None,
    tech_asset_id: int | None = None,
) -> list[Implementation]:
    stmt = select(Implementation).order_by(Implementation.id)
    if control_id is not None:
        stmt = stmt.where(Implementation.control_id == control_id)
    if tech_asset_id is not None:
        stmt = stmt.where(Implementation.tech_asset_id == tech_asset_id)
    return list(await session.scalars(stmt))


async def controls_for_asset(session: AsyncSession, tech_asset_id: int) -> list[ImplementationOut]:
    """一次 join 查出某个资产上已落地的控制点，不适用（N/A）的不算。"""
    rows = await session.execute(
        select(Implementation, Control)
        .join(Control, Control.id == Implementation.control_id)
        .where(
            Implementation.tech_asset_id == tech_asset_id,
            Implementation.status != ImplementationStatus.NOT_APPLICABLE,
        )
        .order_by(Control.code, Implementation.id)
    )
    return [
        ImplementationOut(
            **{
                **ImplementationOut.model_validate(implementation).model_dump(),
                "control_code": control.code,
                "control_title": control.title,
            }
        )
        for implementation, control in rows
    ]


async def implementations_for_controls(
    session: AsyncSession, control_ids: Iterable[int]
) -> dict[int, list[ImplementationOut]]:
    ids = set(control_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(Implementation, Control)
        .join(Control, Control.id == Implementation.control_id)
        .where(Implementation.control_id.in_(ids))
        .order_by(Implementation.control_id, Implementation.id)
    )
    result = {control_id: [] for control_id in ids}
    for implementation, control in rows:
        result[implementation.control_id].append(
            ImplementationOut(
                **{
                    **ImplementationOut.model_validate(implementation).model_dump(),
                    "control_code": control.code,
                    "control_title": control.title,
                }
            )
        )
    return result
