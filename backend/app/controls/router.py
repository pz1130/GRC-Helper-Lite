from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.controls import service
from app.controls.merge import MergePlan, plan_digest, plan_merge
from app.controls.models import Control
from app.controls.schemas import (
    ControlDetailOut,
    ControlOut,
    ControlUpdateIn,
    MergeIn,
    MergePlanOut,
)
from app.db import get_session
from app.environment import service as environment_service
from app.errors import NotFound
from app.evidence import service as evidence_service
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.review.materialize import merge_controls as apply_control_merge
from app.review.materialize import update_control as apply_control_update

router = APIRouter(prefix="/api/controls", tags=["controls"])


@router.get("", response_model=list[ControlOut])
async def list_controls(
    *,
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    _: Annotated[User, Depends(require(Permission.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[Control]:
    return await service.search(session, q=q, limit=limit)


@router.get("/{control_id}", response_model=ControlDetailOut)
async def get_control(
    *,
    control_id: int,
    _: Annotated[User, Depends(require(Permission.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ControlDetailOut:
    control = await session.get(Control, control_id)
    if control is None:
        raise NotFound("控制点不存在", code="control.not_found")
    implementations = await environment_service.implementations_for_controls(session, [control_id])
    evidence = await evidence_service.items_for_controls(session, [control_id])
    return ControlDetailOut(
        **ControlOut.model_validate(control).model_dump(),
        sources=await service.sources(session, control_id),
        relations=await service.relations(session, control_id),
        mappings=await service.mappings(session, control_id),
        implementations=implementations.get(control_id, []),
        evidence=evidence.get(control_id, []),
    )


@router.patch("/{control_id}", response_model=ControlOut)
async def update_control(
    *,
    control_id: int,
    payload: ControlUpdateIn,
    actor: Annotated[User, Depends(require(Permission.CONTROL_WRITE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Control:
    try:
        control = await apply_control_update(
            session, control_id, payload.model_dump(exclude_unset=True), actor=actor
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return control


def _merge_plan_out(plan: MergePlan) -> MergePlanOut:
    return MergePlanOut.model_validate({**asdict(plan), "plan_digest": plan_digest(plan)})


async def _require_pair(session: AsyncSession, loser_id: int, winner_id: int) -> None:
    loser = await session.get(Control, loser_id)
    winner = await session.get(Control, winner_id)
    if loser is None or winner is None:
        raise NotFound("控制点不存在", code="control.not_found")


@router.get("/{control_id}/merge-preview", response_model=MergePlanOut)
async def preview_merge(
    *,
    control_id: int,
    into: Annotated[int, Query(gt=0)],
    _: Annotated[User, Depends(require(Permission.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MergePlanOut:
    await _require_pair(session, control_id, into)
    plan = await plan_merge(session, loser_id=control_id, winner_id=into)
    return _merge_plan_out(plan)


@router.post("/{control_id}/merge", response_model=MergePlanOut)
async def merge_control(
    *,
    control_id: int,
    payload: MergeIn,
    actor: Annotated[User, Depends(require(Permission.CONTROL_WRITE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MergePlanOut:
    try:
        await _require_pair(session, control_id, payload.into_control_id)
        plan = await apply_control_merge(
            session,
            loser_id=control_id,
            winner_id=payload.into_control_id,
            actor=actor,
            expected_digest=payload.plan_digest,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return _merge_plan_out(plan)
