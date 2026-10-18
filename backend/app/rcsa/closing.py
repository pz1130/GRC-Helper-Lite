"""活动的后半程：进入复核、退回、关闭与取消（2D）。

关闭在同一事务里锁住活动与全部评估项，逐条检查阻断项，然后把当时的全部版本、处置
和行动状态固化进 `closed_snapshot`。之后行动验证、接受到期等「当前跟进」照常变化，
但闭期快照与本期评分、回答都不再改——数据库触发器也会拒绝改动已关闭活动这一行。

取消要理由；已有行动继续追踪，取消活动不能顺带取消风险责任。
"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict
from app.iam.models import User
from app.rcsa.access import Command, audit, bump, check_version, lock
from app.rcsa.campaigns import require_owner, require_status
from app.rcsa.models import (
    ActionStatus,
    CampaignStatus,
    Disposition,
    DispositionStatus,
    ItemStatus,
    RcsaAction,
    RcsaCampaign,
    RcsaItem,
    RcsaItemRisk,
    RcsaResponse,
    RcsaResponseEvidence,
    RcsaReview,
    ReviewDecision,
)
from app.rcsa.responses import risk_view
from app.rcsa.schemas import AnswerV1, CloseIn
from app.rcsa.triggers import complete_for_campaign

SNAPSHOT_SCHEMA = 1
_DONE = (ItemStatus.APPROVED, ItemStatus.EXCLUDED)
_OPEN_ACTIONS = (ActionStatus.OPEN, ActionStatus.IN_PROGRESS, ActionStatus.PENDING_VERIFICATION)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _items(session: AsyncSession, campaign_id: int, *, for_update: bool = False) -> list[RcsaItem]:
    statement = select(RcsaItem).where(RcsaItem.campaign_id == campaign_id).order_by(RcsaItem.id)
    if for_update:
        statement = statement.with_for_update()
    return list(await session.scalars(statement))


async def begin_review(session: AsyncSession, campaign_id: int, expected_version: int, actor: User) -> RcsaCampaign:
    campaign = await lock(session, RcsaCampaign, campaign_id, code="rcsa.campaign_not_found")
    require_owner(campaign, actor)
    require_status(campaign, CampaignStatus.ACTIVE)
    check_version(campaign, expected_version)
    unfinished = [item for item in await _items(session, campaign.id, for_update=True) if item.status not in _DONE]
    if unfinished:
        raise Conflict("还有评估项没有复核通过或排除", code="rcsa.items_unfinished",
                       params={"count": len(unfinished)})
    campaign.status = CampaignStatus.IN_REVIEW
    bump(campaign, actor)
    await session.flush()
    await audit(session, actor, "begin_review", "RcsaCampaign", campaign.id,
                {"status": "in_review", "version": campaign.version})
    return campaign


async def return_to_active(
    session: AsyncSession, campaign_id: int, reason: str, expected_version: int, actor: User
) -> RcsaCampaign:
    campaign = await lock(session, RcsaCampaign, campaign_id, code="rcsa.campaign_not_found")
    require_owner(campaign, actor)
    require_status(campaign, CampaignStatus.IN_REVIEW)
    check_version(campaign, expected_version)
    campaign.status = CampaignStatus.ACTIVE
    campaign.status_reason = reason
    bump(campaign, actor)
    await session.flush()
    await audit(session, actor, "return", "RcsaCampaign", campaign.id,
                {"status": "active", "reason": reason, "version": campaign.version})
    return campaign


async def cancel(
    session: AsyncSession, campaign_id: int, reason: str, expected_version: int, actor: User
) -> RcsaCampaign:
    campaign = await lock(session, RcsaCampaign, campaign_id, code="rcsa.campaign_not_found")
    require_owner(campaign, actor)
    require_status(campaign, CampaignStatus.DRAFT, CampaignStatus.ACTIVE, CampaignStatus.IN_REVIEW)
    check_version(campaign, expected_version)
    campaign.status = CampaignStatus.CANCELLED
    campaign.status_reason = reason
    bump(campaign, actor)
    await session.flush()
    await audit(session, actor, "cancel", "RcsaCampaign", campaign.id,
                {"status": "cancelled", "reason": reason, "version": campaign.version})
    return campaign


# ── 关闭 ────────────────────────────────────────────────────


def _blocker(code: str, **context: Any) -> dict[str, Any]:
    return {"code": code} | context


def _expired_since_submission(answer: dict[str, Any], evidence: list[RcsaResponseEvidence], today: date) -> bool:
    if AnswerV1.model_validate(answer).evidence_limitations_acknowledged:
        return False
    return any(
        row.snapshot.get("valid_until") and date.fromisoformat(row.snapshot["valid_until"]) < today
        for row in evidence
    )


async def close(
    session: AsyncSession, campaign_id: int, payload: CloseIn, actor: User, client_key: str
) -> RcsaCampaign:
    campaign = await lock(session, RcsaCampaign, campaign_id, code="rcsa.campaign_not_found")
    command = Command(actor, "close", "RcsaCampaign", campaign.id, client_key, payload.model_dump())
    if await command.replay(session) is not None:
        return campaign
    require_owner(campaign, actor)
    require_status(campaign, CampaignStatus.IN_REVIEW)
    check_version(campaign, payload.expected_version)

    today = _now().date()
    items = await _items(session, campaign.id, for_update=True)
    snapshot_items = []
    blockers: list[dict[str, Any]] = []
    overdue = evidence_expired = open_actions = 0
    for item in items:
        code = (item.control_snapshot or {}).get("code")
        if item.status not in _DONE:
            blockers.append(_blocker(code="rcsa.blocked.item_unfinished", item_id=item.id, control_code=code))
        entry, evidence = await _item_snapshot(session, item)
        snapshot_items.append(entry)
        if item.status != ItemStatus.APPROVED:
            continue
        if entry["response"] and _expired_since_submission(entry["response"]["answer"], evidence, today):
            evidence_expired += 1
        for risk in entry["risks"]:
            if risk["disposition_status"] != DispositionStatus.APPROVED:
                blockers.append(_blocker(code="rcsa.blocked.disposition_unapproved", risk_id=risk["id"],
                                         control_code=code))
            elif risk["disposition"] == Disposition.ACCEPT and date.fromisoformat(risk["accept_until"]) < today:
                blockers.append(_blocker(code="rcsa.blocked.acceptance_expired", risk_id=risk["id"],
                                         control_code=code))
            for action in risk["actions"]:
                if action["status"] in _OPEN_ACTIONS:
                    open_actions += 1
                    overdue += date.fromisoformat(action["due_at"]) < today
    if overdue and not payload.overdue_acknowledgement:
        blockers.append(_blocker(code="rcsa.blocked.overdue_unacknowledged", count=overdue))
    if evidence_expired and not payload.evidence_acknowledgement:
        blockers.append(_blocker(code="rcsa.blocked.evidence_expired", count=evidence_expired))
    if blockers:
        raise Conflict("活动还不能关闭", code="rcsa.close_blocked", params={"blockers": blockers})

    closed_at = _now()
    campaign.closed_snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "closed_at": closed_at.isoformat(),
        "closed_by": actor.id,
        "campaign": {
            "id": campaign.id, "name": campaign.name, "period_start": _iso(campaign.period_start),
            "period_end": _iso(campaign.period_end), "due_at": _iso(campaign.due_at),
            "timezone": campaign.timezone, "owner_id": campaign.owner_id,
            "framework": campaign.framework_snapshot, "profile": campaign.profile_snapshot,
        },
        "items": snapshot_items,
        "open_actions": open_actions,
        "overdue_actions": overdue,
        "acknowledgements": {
            "overdue": payload.overdue_acknowledgement, "evidence": payload.evidence_acknowledgement,
        },
    }
    campaign.status = CampaignStatus.CLOSED
    campaign.closed_by = actor.id
    campaign.closed_at = closed_at
    bump(campaign, actor)
    await session.flush()
    completed = await complete_for_campaign(session, campaign.id, actor)
    await command.done(session, campaign.id, {"status": "closed", "version": campaign.version,
                                              "open_actions": open_actions, "triggers_completed": completed})
    return campaign


async def _item_snapshot(
    session: AsyncSession, item: RcsaItem
) -> tuple[dict[str, Any], list[RcsaResponseEvidence]]:
    """评估项的闭期快照，以及它当前已批准版本的证据行（关闭检查要用）。"""
    response = await session.get(RcsaResponse, item.current_response_id) if item.current_response_id else None
    response_ids = select(RcsaResponse.id).where(RcsaResponse.item_id == item.id)
    reviews = list(await session.scalars(
        select(RcsaReview)
        .where((RcsaReview.item_id == item.id) | RcsaReview.response_id.in_(response_ids))
        .order_by(RcsaReview.id)
    ))
    exclusion = next((r for r in reviews if r.decision == ReviewDecision.EXCLUDE), None)
    risks = list(await session.scalars(
        select(RcsaItemRisk).where(RcsaItemRisk.item_id == item.id).order_by(RcsaItemRisk.id).with_for_update()
    ))
    entry: dict[str, Any] = {
        "id": item.id, "status": str(item.status), "control": item.control_snapshot,
        "mappings": item.mapping_snapshot, "sources": item.source_snapshot,
        "assignee_id": item.assignee_id, "reviewer_id": item.reviewer_id,
        "response": None,
        "exclusion": {"reason": exclusion.reason, "reviewer_id": exclusion.reviewer_id,
                      "at": exclusion.at.isoformat()} if exclusion else None,
        "reviews": [{"decision": str(r.decision), "target_version": r.target_version,
                     "reviewer_id": r.reviewer_id, "reason": r.reason, "at": r.at.isoformat()} for r in reviews],
        "risks": [await _risk_snapshot(session, risk) for risk in risks],
    }
    evidence: list[RcsaResponseEvidence] = []
    if response is not None and item.status == ItemStatus.APPROVED:
        evidence = list(await session.scalars(
            select(RcsaResponseEvidence).where(RcsaResponseEvidence.response_id == response.id)
            .order_by(RcsaResponseEvidence.id)
        ))
        entry["response"] = {
            "id": response.id, "revision": response.revision, "answer_schema": response.answer_schema,
            "answer": response.answer, "risk_snapshot": response.risk_snapshot,
            "submitted_by": response.submitted_by, "submitted_at": _iso(response.submitted_at),
            "evidence": [{
                "purpose": str(row.purpose), "evidence_item_id": row.evidence_item_id,
                "external_location": row.external_location, "snapshot": row.snapshot,
                "tester_user_id": row.tester_user_id, "tester_name": row.tester_name,
                "tested_at": _iso(row.tested_at), "test_scope": row.test_scope, "conclusion": row.conclusion,
                "independence_basis": row.independence_basis,
            } for row in evidence],
        }
    return entry, evidence


async def _risk_snapshot(session: AsyncSession, risk: RcsaItemRisk) -> dict[str, Any]:
    actions = list(await session.scalars(
        select(RcsaAction).where(RcsaAction.item_risk_id == risk.id).order_by(RcsaAction.id).with_for_update()
    ))
    return risk_view(risk) | {
        "disposition": str(risk.disposition) if risk.disposition else None,
        "disposition_status": str(risk.disposition_status),
        "disposition_revision": risk.disposition_revision,
        "disposition_details": risk.disposition_details,
        "accept_until": _iso(risk.accept_until), "reassess_at": _iso(risk.reassess_at),
        "register_risk_id": risk.register_risk_id,
        "actions": [{
            "id": a.id, "title": a.title, "owner_id": a.owner_id, "verifier_id": a.verifier_id,
            "due_at": _iso(a.due_at), "status": str(a.status), "success_criteria": a.success_criteria,
            "verified_at": _iso(a.verified_at),
        } for a in actions],
    }
