from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import Conflict, NotFound
from app.frameworks.models import FrameworkItem
from app.iam.audit import record
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.maturity.models import MaturityAssessment, MaturityScore
from app.risk.models import RiskEntry, RiskSource, RiskStatus
from app.risk.schemas import (
    GapRiskCreateIn,
    RiskCreateIn,
    RiskOut,
    RiskOwnerOut,
    RiskUpdateIn,
)

router = APIRouter(prefix="/api/risks", tags=["risks"])
Reader = Annotated[User, Depends(require(Permission.READ))]
Writer = Annotated[User, Depends(require(Permission.RISK_WRITE))]
Session = Annotated[AsyncSession, Depends(get_session)]


def _snapshot(row: RiskEntry) -> dict:
    return {
        "title": row.title,
        "likelihood": row.likelihood,
        "impact": row.impact,
        "inherent_score": row.inherent_score,
        "residual_likelihood": row.residual_likelihood,
        "residual_impact": row.residual_impact,
        "residual_score": row.residual_score,
        "owner_user_id": row.owner_user_id,
        "due_date": row.due_date.isoformat() if row.due_date else None,
        "status": row.status.value,
    }


async def _validate_owner(session: AsyncSession, owner_user_id: int | None) -> None:
    if owner_user_id is not None and await session.get(User, owner_user_id) is None:
        raise NotFound("责任人不存在", code="risk.owner_not_found")


@router.get("", response_model=list[RiskOut])
async def list_risks(
    actor: Reader,
    session: Session,
    risk_status: Annotated[RiskStatus | None, Query(alias="status")] = None,
) -> list[RiskEntry]:
    statement = select(RiskEntry).order_by(RiskEntry.inherent_score.desc(), RiskEntry.id.desc())
    if risk_status is not None:
        statement = statement.where(RiskEntry.status == risk_status)
    return list(await session.scalars(statement))


@router.get("/owners", response_model=list[RiskOwnerOut])
async def list_risk_owners(actor: Reader, session: Session) -> list[RiskOwnerOut]:
    rows = await session.execute(
        select(User.id, User.name, User.email).where(User.is_active.is_(True)).order_by(User.name)
    )
    return [RiskOwnerOut(id=row.id, name=row.name, email=row.email) for row in rows]


@router.post("", response_model=RiskOut, status_code=status.HTTP_201_CREATED)
async def create_risk(
    payload: RiskCreateIn, request: Request, actor: Writer, session: Session
) -> RiskEntry:
    await _validate_owner(session, payload.owner_user_id)
    data = payload.model_dump()
    row = RiskEntry(
        **data,
        source=RiskSource.MANUAL,
        source_ref={},
        inherent_score=payload.likelihood * payload.impact,
        residual_score=(
            payload.residual_likelihood * payload.residual_impact
            if payload.residual_likelihood is not None and payload.residual_impact is not None
            else None
        ),
        status=RiskStatus.OPEN,
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        user=actor,
        action="risk.create",
        entity_type="RiskEntry",
        entity_id=row.id,
        after=_snapshot(row),
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return row


@router.post("/from-maturity-gap", response_model=RiskOut, status_code=status.HTTP_201_CREATED)
async def create_from_maturity_gap(
    payload: GapRiskCreateIn, request: Request, actor: Writer, session: Session
) -> RiskEntry:
    assessment = await session.get(MaturityAssessment, payload.assessment_id)
    item = await session.get(FrameworkItem, payload.framework_item_id)
    if assessment is None or item is None or item.framework_id != assessment.framework_id:
        raise NotFound("成熟度差距不存在", code="maturity.gap_not_found")
    score = await session.scalar(
        select(MaturityScore).where(
            MaturityScore.assessment_id == assessment.id,
            MaturityScore.framework_item_id == item.id,
        )
    )
    if score is None or score.impl_score >= 3:
        raise Conflict("只有落地分低于 3 的已评分条目才能转为风险", code="risk.gap_not_eligible")
    duplicate = await session.scalar(
        select(RiskEntry).where(
            RiskEntry.source == RiskSource.GAP,
            RiskEntry.framework_item_id == item.id,
            RiskEntry.source_ref["assessment_id"].as_integer() == assessment.id,
        )
    )
    if duplicate is not None:
        raise Conflict("该成熟度差距已经生成风险", code="risk.gap_already_converted")

    impact = max(2, 5 - score.impl_score)
    row = RiskEntry(
        title=f"{item.code} 落地成熟度差距",
        description=score.impl_rationale or item.title,
        source=RiskSource.GAP,
        source_ref={"assessment_id": assessment.id, "framework_item_id": item.id},
        framework_item_id=item.id,
        likelihood=3,
        impact=impact,
        inherent_score=3 * impact,
        mitigation="",
        owner_user_id=actor.id,
        due_date=assessment.as_of_date + timedelta(days=90),
        status=RiskStatus.OPEN,
    )
    session.add(row)
    await session.flush()
    await record(
        session,
        user=actor,
        action="risk.create_from_gap",
        entity_type="RiskEntry",
        entity_id=row.id,
        after=_snapshot(row) | {"source_ref": row.source_ref},
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return row


@router.patch("/{risk_id}", response_model=RiskOut)
async def update_risk(
    risk_id: Annotated[int, Path(gt=0)],
    payload: RiskUpdateIn,
    request: Request,
    actor: Writer,
    session: Session,
) -> RiskEntry:
    row = await session.get(RiskEntry, risk_id)
    if row is None:
        raise NotFound("风险条目不存在", code="risk.not_found")
    before = _snapshot(row)
    data = payload.model_dump(exclude_unset=True)
    await _validate_owner(session, data.get("owner_user_id"))
    for field, value in data.items():
        setattr(row, field, value)
    row.inherent_score = row.likelihood * row.impact
    if (row.residual_likelihood is None) != (row.residual_impact is None):
        raise Conflict("剩余可能性和剩余影响必须同时填写", code="risk.residual_pair_required")
    row.residual_score = (
        row.residual_likelihood * row.residual_impact
        if row.residual_likelihood is not None and row.residual_impact is not None
        else None
    )
    await session.flush()
    await session.refresh(row)
    await record(
        session,
        user=actor,
        action="risk.update",
        entity_type="RiskEntry",
        entity_id=row.id,
        before=before,
        after=_snapshot(row),
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return row
