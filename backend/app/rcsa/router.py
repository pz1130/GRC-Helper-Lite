"""/api/rcsa 路由。状态转换一律是显式的 POST，没有能改任意 status 的 PATCH。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.rcsa import campaigns, responses
from app.rcsa.access import (
    ClientKey,
    Manager,
    Reader,
    Responder,
    ReviewerUser,
    fetch,
    module_enabled,
)
from app.rcsa.models import (
    CampaignStatus,
    RcsaCampaign,
    RcsaItem,
    RcsaResponse,
    RcsaResponseEvidence,
    RcsaReview,
    RcsaScoringProfile,
)
from app.rcsa.schemas import (
    AnswerIn,
    AssignIn,
    CampaignCreateIn,
    CampaignOut,
    CampaignUpdateIn,
    EvidenceIn,
    EvidenceOut,
    ItemOut,
    ItemsAddIn,
    Page,
    ProfileCreateIn,
    ProfileOut,
    ProfileUpdateIn,
    ReasonIn,
    ResponseOut,
    ReviewIn,
    ReviewOut,
    ScopeRow,
    VersionIn,
)

Session = Annotated[AsyncSession, Depends(get_session)]
RowId = Annotated[int, Path(gt=0)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]

# 开关关闭时前端仍要知道「关着」，所以 status 不挂在受开关保护的路由上。
status_router = APIRouter(prefix="/api/rcsa", tags=["rcsa"])
router = APIRouter(prefix="/api/rcsa", tags=["rcsa"], dependencies=[Depends(module_enabled)])


@status_router.get("/status")
async def rcsa_status(actor: Reader) -> dict[str, bool]:
    return {"enabled": get_settings().rcsa_enabled}


# ── 评分档案 ────────────────────────────────────────────────


@router.get("/profiles", response_model=list[ProfileOut])
async def list_profiles(actor: Reader, session: Session) -> list[RcsaScoringProfile]:
    from sqlalchemy import select

    return list(
        await session.scalars(
            select(RcsaScoringProfile).order_by(RcsaScoringProfile.key, RcsaScoringProfile.revision)
        )
    )


@router.get("/profiles/{profile_id}", response_model=ProfileOut)
async def get_profile(profile_id: RowId, actor: Reader, session: Session) -> RcsaScoringProfile:
    return await fetch(session, RcsaScoringProfile, profile_id, code="rcsa.profile_not_found")


@router.post("/profiles", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
async def create_profile(payload: ProfileCreateIn, actor: Manager, session: Session) -> RcsaScoringProfile:
    profile = await campaigns.create_profile(session, payload, actor)
    await session.commit()
    return profile


@router.patch("/profiles/{profile_id}", response_model=ProfileOut)
async def update_profile(
    profile_id: RowId, payload: ProfileUpdateIn, actor: Manager, session: Session
) -> RcsaScoringProfile:
    profile = await campaigns.update_profile(session, profile_id, payload, actor)
    await session.commit()
    return profile


@router.post("/profiles/{profile_id}/approve", response_model=ProfileOut)
async def approve_profile(
    profile_id: RowId, payload: VersionIn, actor: Manager, session: Session
) -> RcsaScoringProfile:
    profile = await campaigns.approve_profile(session, profile_id, payload.expected_version, actor)
    await session.commit()
    return profile


# ── 活动 ────────────────────────────────────────────────────


@router.get("/campaigns", response_model=Page[CampaignOut])
async def list_campaigns(
    actor: Reader,
    session: Session,
    campaign_status: Annotated[CampaignStatus | None, Query(alias="status")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[CampaignOut]:
    rows, total = await campaigns.list_campaigns(session, campaign_status, limit, offset)
    return Page(items=rows, total=total)


@router.post("/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
async def create_campaign(payload: CampaignCreateIn, actor: Manager, session: Session) -> CampaignOut:
    campaign = await campaigns.create_campaign(session, payload, actor)
    await session.commit()
    return await campaigns.campaign_out(session, campaign)


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: RowId, actor: Reader, session: Session) -> CampaignOut:
    campaign = await fetch(session, RcsaCampaign, campaign_id, code="rcsa.campaign_not_found")
    return await campaigns.campaign_out(session, campaign)


@router.patch("/campaigns/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    campaign_id: RowId, payload: CampaignUpdateIn, actor: Manager, session: Session
) -> CampaignOut:
    campaign = await campaigns.update_campaign(session, campaign_id, payload, actor)
    await session.commit()
    return await campaigns.campaign_out(session, campaign)


@router.get("/campaigns/{campaign_id}/scope", response_model=list[ScopeRow])
async def campaign_scope(campaign_id: RowId, actor: Reader, session: Session) -> list[ScopeRow]:
    return await campaigns.scope(session, campaign_id)


@router.get("/campaigns/{campaign_id}/items", response_model=list[ItemOut])
async def campaign_items(campaign_id: RowId, actor: Reader, session: Session) -> list[RcsaItem]:
    return await campaigns.list_items(session, campaign_id)


@router.post(
    "/campaigns/{campaign_id}/items", response_model=list[ItemOut], status_code=status.HTTP_201_CREATED
)
async def add_items(
    campaign_id: RowId, payload: ItemsAddIn, actor: Manager, session: Session
) -> list[RcsaItem]:
    items = await campaigns.add_items(session, campaign_id, payload, actor)
    await session.commit()
    return items


@router.post("/campaigns/{campaign_id}/activate", response_model=CampaignOut)
async def activate_campaign(
    campaign_id: RowId, payload: VersionIn, actor: Manager, key: ClientKey, session: Session
) -> CampaignOut:
    campaign = await campaigns.activate(session, campaign_id, payload.expected_version, actor, key)
    await session.commit()
    return await campaigns.campaign_out(session, campaign)


# ── 评估项 ──────────────────────────────────────────────────


@router.get("/items/{item_id}", response_model=ItemOut)
async def get_item(item_id: RowId, actor: Reader, session: Session) -> RcsaItem:
    return await fetch(session, RcsaItem, item_id, code="rcsa.item_not_found")


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_item(item_id: RowId, actor: Manager, session: Session) -> None:
    await campaigns.remove_item(session, item_id, actor)
    await session.commit()


@router.post("/items/{item_id}/assign", response_model=ItemOut)
async def assign_item(item_id: RowId, payload: AssignIn, actor: Manager, session: Session) -> RcsaItem:
    item = await campaigns.assign(session, item_id, payload, actor)
    await session.commit()
    return item


# ── 作答与复核 ──────────────────────────────────────────────


@router.get("/items/{item_id}/responses", response_model=list[ResponseOut])
async def item_responses(item_id: RowId, actor: Reader, session: Session) -> list[ResponseOut]:
    return [await responses.response_out(session, row) for row in await responses.history(session, item_id)]


@router.post("/items/{item_id}/responses", response_model=ResponseOut, status_code=status.HTTP_201_CREATED)
async def open_draft(item_id: RowId, payload: VersionIn, actor: Responder, session: Session) -> ResponseOut:
    response = await responses.open_draft(session, item_id, payload.expected_version, actor)
    await session.commit()
    return await responses.response_out(session, response)


@router.get("/items/{item_id}/reviews", response_model=list[ReviewOut])
async def item_reviews(item_id: RowId, actor: Reader, session: Session) -> list[RcsaReview]:
    return await responses.item_reviews(session, item_id)


@router.post("/items/{item_id}/exclude", response_model=ItemOut)
async def exclude_item(
    item_id: RowId, payload: ReasonIn, actor: ReviewerUser, key: ClientKey, session: Session
) -> RcsaItem:
    item = await responses.exclude(session, item_id, payload.reason, payload.expected_version, actor, key)
    await session.commit()
    return item


@router.get("/responses/{response_id}", response_model=ResponseOut)
async def get_response(response_id: RowId, actor: Reader, session: Session) -> ResponseOut:
    response = await fetch(session, RcsaResponse, response_id, code="rcsa.response_not_found")
    return await responses.response_out(session, response)


@router.patch("/responses/{response_id}", response_model=ResponseOut)
async def save_answer(response_id: RowId, payload: AnswerIn, actor: Responder, session: Session) -> ResponseOut:
    response = await responses.save_answer(session, response_id, payload.answer, payload.expected_version, actor)
    await session.commit()
    return await responses.response_out(session, response)


@router.post(
    "/responses/{response_id}/evidence", response_model=EvidenceOut, status_code=status.HTTP_201_CREATED
)
async def attach_evidence(
    response_id: RowId, payload: EvidenceIn, actor: Responder, session: Session
) -> RcsaResponseEvidence:
    row = await responses.attach_evidence(session, response_id, payload, actor)
    await session.commit()
    return row


@router.delete("/responses/{response_id}/evidence/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def detach_evidence(
    response_id: RowId, evidence_id: RowId, actor: Responder, session: Session
) -> None:
    await responses.detach_evidence(session, response_id, evidence_id, actor)
    await session.commit()


@router.post("/responses/{response_id}/submit", response_model=ResponseOut)
async def submit_response(
    response_id: RowId, payload: VersionIn, actor: Responder, key: ClientKey, session: Session
) -> ResponseOut:
    response = await responses.submit(session, response_id, payload.expected_version, actor, key)
    await session.commit()
    return await responses.response_out(session, response)


@router.post("/responses/{response_id}/review", response_model=ResponseOut)
async def review_response(
    response_id: RowId, payload: ReviewIn, actor: ReviewerUser, key: ClientKey, session: Session
) -> ResponseOut:
    response = await responses.review(session, response_id, payload, actor, key)
    await session.commit()
    return await responses.response_out(session, response)
