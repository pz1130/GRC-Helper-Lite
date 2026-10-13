from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import Conflict, NotFound
from app.evidence import service
from app.evidence.models import EvidenceItem, EvidenceType
from app.evidence.schemas import (
    EvidenceItemCreateIn,
    EvidenceItemOut,
    EvidenceItemUpdateIn,
    EvidenceTypeCreateIn,
    EvidenceTypeOut,
    EvidenceTypeUpdateIn,
)
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission

router = APIRouter(tags=["evidence"])
Reader = Annotated[User, Depends(require(Permission.READ))]
Writer = Annotated[User, Depends(require(Permission.EVIDENCE_WRITE))]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/api/evidence-types", response_model=list[EvidenceTypeOut])
async def list_evidence_types(_: Reader, session: Session) -> list[EvidenceType]:
    return await service.list_types(session)


@router.post(
    "/api/evidence-types", response_model=EvidenceTypeOut, status_code=status.HTTP_201_CREATED
)
async def create_evidence_type(
    payload: EvidenceTypeCreateIn,
    request: Request,
    actor: Writer,
    session: Session,
) -> EvidenceType:
    evidence_type = await service.create_type(
        session,
        payload.model_dump(),
        actor=actor,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return evidence_type


@router.get("/api/evidence-types/{evidence_type_id}", response_model=EvidenceTypeOut)
async def get_evidence_type(
    evidence_type_id: Annotated[int, Path(gt=0)], _: Reader, session: Session
) -> EvidenceType:
    evidence_type = await session.get(EvidenceType, evidence_type_id)
    if evidence_type is None:
        raise NotFound("证据类型不存在", code="evidence_type.not_found")
    return evidence_type


@router.patch("/api/evidence-types/{evidence_type_id}", response_model=EvidenceTypeOut)
async def update_evidence_type(
    evidence_type_id: Annotated[int, Path(gt=0)],
    payload: EvidenceTypeUpdateIn,
    request: Request,
    actor: Writer,
    session: Session,
) -> EvidenceType:
    evidence_type = await session.get(EvidenceType, evidence_type_id)
    if evidence_type is None:
        raise NotFound("证据类型不存在", code="evidence_type.not_found")
    updated = await service.update_type(
        session,
        evidence_type,
        payload.model_dump(exclude_unset=True),
        actor=actor,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return updated


@router.delete("/api/evidence-types/{evidence_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_evidence_type(
    evidence_type_id: Annotated[int, Path(gt=0)],
    request: Request,
    actor: Writer,
    session: Session,
) -> None:
    evidence_type = await session.get(EvidenceType, evidence_type_id)
    if evidence_type is None:
        raise NotFound("证据类型不存在", code="evidence_type.not_found")
    # 被证据引用时明确拒绝。数据库那条外键已经是 RESTRICT 会拦住，但那样抛出来的
    # 是一条 IntegrityError，对用户毫无帮助——这里先查一次，把"还有几条证据在用它"
    # 直接说出来（与 provider 删除的做法一致）。
    in_use = await session.scalar(
        select(func.count()).select_from(EvidenceItem).where(
            EvidenceItem.evidence_type_id == evidence_type_id
        )
    )
    if in_use:
        raise Conflict(
            f"还有 {in_use} 条证据在用这个类型，请先改掉它们的类型或删除那些证据",
            code="evidence_type.in_use",
            params={"count": in_use},
        )
    await service.delete_type(
        session, evidence_type, actor=actor, ip=request.client.host if request.client else None
    )
    await session.commit()


@router.get("/api/evidence", response_model=list[EvidenceItemOut])
async def list_evidence(
    _: Reader,
    session: Session,
    owner_user_id: int | None = Query(default=None, gt=0),
    status_filter: str | None = Query(default=None, alias="status"),
    display_status: str | None = Query(default=None),
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[EvidenceItemOut]:
    return await service.list_items(
        session,
        owner_user_id=owner_user_id,
        display_status=display_status or status_filter,
        due_before=due_before,
        due_after=due_after,
        limit=limit,
    )


@router.get("/api/evidence/stats")
async def evidence_stats(_: Reader, session: Session) -> dict[str, int]:
    return {"expired": await service.expired_count(session)}


@router.post("/api/evidence", response_model=EvidenceItemOut, status_code=status.HTTP_201_CREATED)
async def create_evidence(
    payload: EvidenceItemCreateIn,
    request: Request,
    actor: Writer,
    session: Session,
) -> EvidenceItemOut:
    item = await service.create_item(
        session,
        payload.model_dump(),
        actor=actor,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return service.item_out(item)


@router.get("/api/evidence/{evidence_id}", response_model=EvidenceItemOut)
async def get_evidence(
    evidence_id: Annotated[int, Path(gt=0)], _: Reader, session: Session
) -> EvidenceItemOut:
    item = await session.get(EvidenceItem, evidence_id)
    if item is None:
        raise NotFound("证据不存在", code="evidence.not_found")
    return service.item_out(item)


@router.patch("/api/evidence/{evidence_id}", response_model=EvidenceItemOut)
async def update_evidence(
    evidence_id: Annotated[int, Path(gt=0)],
    payload: EvidenceItemUpdateIn,
    request: Request,
    actor: Writer,
    session: Session,
) -> EvidenceItemOut:
    item = await session.get(EvidenceItem, evidence_id)
    if item is None:
        raise NotFound("证据不存在", code="evidence.not_found")
    updated = await service.update_item(
        session,
        item,
        payload.model_dump(exclude_unset=True),
        actor=actor,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return service.item_out(updated)


@router.delete("/api/evidence/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_evidence(
    evidence_id: Annotated[int, Path(gt=0)],
    request: Request,
    actor: Writer,
    session: Session,
) -> None:
    item = await session.get(EvidenceItem, evidence_id)
    if item is None:
        raise NotFound("证据不存在", code="evidence.not_found")
    await service.delete_item(
        session, item, actor=actor, ip=request.client.host if request.client else None
    )
    await session.commit()
