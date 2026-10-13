from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.environment import service
from app.environment.models import Implementation, TechAsset
from app.environment.schemas import (
    ImplementationCreateIn,
    ImplementationOut,
    ImplementationUpdateIn,
    TechAssetCreateIn,
    TechAssetOut,
    TechAssetUpdateIn,
)
from app.errors import Conflict, NotFound
from app.evidence.models import EvidenceItem
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission

router = APIRouter(tags=["environment"])
Reader = Annotated[User, Depends(require(Permission.READ))]
Writer = Annotated[User, Depends(require(Permission.ENVIRONMENT_WRITE))]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/api/tech-assets", response_model=list[TechAssetOut])
async def list_tech_assets(_: Reader, session: Session) -> list[TechAsset]:
    return await service.list_assets(session)


@router.post("/api/tech-assets", response_model=TechAssetOut, status_code=status.HTTP_201_CREATED)
async def create_tech_asset(
    payload: TechAssetCreateIn,
    request: Request,
    actor: Writer,
    session: Session,
) -> TechAsset:
    asset = await service.create_asset(
        session,
        payload.model_dump(),
        actor=actor,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return asset


@router.get("/api/tech-assets/{asset_id}", response_model=TechAssetOut)
async def get_tech_asset(
    asset_id: Annotated[int, Path(gt=0)], _: Reader, session: Session
) -> TechAsset:
    asset = await session.get(TechAsset, asset_id)
    if asset is None:
        raise NotFound("技术资产不存在", code="tech_asset.not_found")
    return asset


@router.patch("/api/tech-assets/{asset_id}", response_model=TechAssetOut)
async def update_tech_asset(
    asset_id: Annotated[int, Path(gt=0)],
    payload: TechAssetUpdateIn,
    request: Request,
    actor: Writer,
    session: Session,
) -> TechAsset:
    asset = await session.get(TechAsset, asset_id)
    if asset is None:
        raise NotFound("技术资产不存在", code="tech_asset.not_found")
    updated = await service.update_asset(
        session,
        asset,
        payload.model_dump(exclude_unset=True),
        actor=actor,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return updated


@router.delete("/api/tech-assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tech_asset(
    asset_id: Annotated[int, Path(gt=0)],
    request: Request,
    actor: Writer,
    session: Session,
) -> None:
    asset = await session.get(TechAsset, asset_id)
    if asset is None:
        raise NotFound("技术资产不存在", code="tech_asset.not_found")
    # 被引用时明确拒绝（外键也是 RESTRICT，见 0028）：原来 SET NULL，删一个资产就
    # 悄悄把引用它的控制点实现与证据的资产清空，这些改动不留审计。
    implementations = await session.scalar(
        select(func.count()).select_from(Implementation).where(Implementation.tech_asset_id == asset_id)
    )
    evidence = await session.scalar(
        select(func.count()).select_from(EvidenceItem).where(EvidenceItem.tech_asset_id == asset_id)
    )
    if implementations or evidence:
        raise Conflict(
            f"还有 {implementations} 条控制点实现、{evidence} 条证据在用这个技术资产",
            code="tech_asset.in_use",
            params={"implementations": implementations, "evidence": evidence},
        )
    await service.delete_asset(
        session, asset, actor=actor, ip=request.client.host if request.client else None
    )
    await session.commit()


@router.get("/api/tech-assets/{asset_id}/controls", response_model=list[ImplementationOut])
async def list_asset_controls(
    asset_id: Annotated[int, Path(gt=0)], _: Reader, session: Session
) -> list[ImplementationOut]:
    if await session.get(TechAsset, asset_id) is None:
        raise NotFound("技术资产不存在", code="tech_asset.not_found")
    return await service.controls_for_asset(session, asset_id)


@router.get("/api/implementations", response_model=list[ImplementationOut])
async def list_implementations(
    _: Reader,
    session: Session,
    control_id: int | None = None,
    tech_asset_id: int | None = None,
) -> list[Implementation]:
    return await service.list_implementations(
        session, control_id=control_id, tech_asset_id=tech_asset_id
    )


@router.post(
    "/api/implementations", response_model=ImplementationOut, status_code=status.HTTP_201_CREATED
)
async def create_implementation(
    payload: ImplementationCreateIn,
    request: Request,
    actor: Writer,
    session: Session,
) -> Implementation:
    implementation = await service.create_implementation(
        session,
        payload.model_dump(),
        actor=actor,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return implementation


@router.get("/api/implementations/{implementation_id}", response_model=ImplementationOut)
async def get_implementation(
    implementation_id: Annotated[int, Path(gt=0)], _: Reader, session: Session
) -> Implementation:
    implementation = await session.get(Implementation, implementation_id)
    if implementation is None:
        raise NotFound("落地实现不存在", code="implementation.not_found")
    return implementation


@router.patch("/api/implementations/{implementation_id}", response_model=ImplementationOut)
async def update_implementation(
    implementation_id: Annotated[int, Path(gt=0)],
    payload: ImplementationUpdateIn,
    request: Request,
    actor: Writer,
    session: Session,
) -> Implementation:
    implementation = await session.get(Implementation, implementation_id)
    if implementation is None:
        raise NotFound("落地实现不存在", code="implementation.not_found")
    updated = await service.update_implementation(
        session,
        implementation,
        payload.model_dump(exclude_unset=True),
        actor=actor,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return updated


@router.delete("/api/implementations/{implementation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_implementation(
    implementation_id: Annotated[int, Path(gt=0)],
    request: Request,
    actor: Writer,
    session: Session,
) -> None:
    implementation = await session.get(Implementation, implementation_id)
    if implementation is None:
        raise NotFound("落地实现不存在", code="implementation.not_found")
    await service.delete_implementation(
        session,
        implementation,
        actor=actor,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
