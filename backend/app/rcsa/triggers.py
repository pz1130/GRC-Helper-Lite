"""手动触发：登记 → 分诊 → 决定重评或不重评 → 完成（2D）。

系统只保证登记之后有可追踪的链路，不承诺发现未登记的事件，也不阻止外部的实际变更。
重大变更的登记不代表获准实施。

- triage：确认受影响控制，空范围不能往下走；
- reassess：关联一个包含受影响控制的活动（不在其中的逐项写排除理由），该活动**关闭**
  时触发才完成——活动取消或未关闭都不算；
- no_reassessment：要书面理由，由另一位有复核权限的人确认后才完成。
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.controls.models import Control
from app.errors import Conflict, Forbidden, NotFound
from app.iam.models import User
from app.iam.permissions import Permission, has_permission
from app.rcsa.access import audit, bump, check_version, ensure, fetch, lock
from app.rcsa.models import (
    CampaignStatus,
    CoverageDisposition,
    RcsaCampaign,
    RcsaItem,
    RcsaReview,
    RcsaTrigger,
    RcsaTriggerControl,
    ReviewDecision,
    TriggerDisposition,
    TriggerStatus,
)
from app.rcsa.schemas import (
    TriggerControlIn,
    TriggerControlOut,
    TriggerDecideIn,
    TriggerIn,
    TriggerLinkIn,
    TriggerOut,
    TriggerReviewIn,
    TriggerUpdateIn,
)


def _now() -> datetime:
    return datetime.now(UTC)


async def trigger_out(session: AsyncSession, trigger: RcsaTrigger) -> TriggerOut:
    out = TriggerOut.model_validate(trigger)
    out.controls = [TriggerControlOut.model_validate(row) for row in await _controls(session, trigger.id)]
    return out


async def list_triggers(
    session: AsyncSession, status: TriggerStatus | None, limit: int, offset: int
) -> tuple[list[RcsaTrigger], int]:
    statement = select(RcsaTrigger)
    if status is not None:
        statement = statement.where(RcsaTrigger.status == status)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = list(await session.scalars(statement.order_by(RcsaTrigger.id.desc()).limit(limit).offset(offset)))
    return rows, int(total or 0)


async def _owner(session: AsyncSession, user_id: int) -> None:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("用户不存在", code="rcsa.user_not_found", params={"user_id": user_id})
    if not user.is_active or not has_permission(user.role, Permission.RCSA_RESPOND) or user.engagement_scope_id:
        raise Conflict(
            "这个人没有对应权限或已停用",
            code="rcsa.person_unavailable",
            params={"user_id": user_id, "permission": Permission.RCSA_RESPOND.value},
        )


async def _control_row(session: AsyncSession, trigger_id: int, spec: TriggerControlIn) -> RcsaTriggerControl:
    control = await fetch(session, Control, spec.control_id, code="rcsa.control_not_found")
    if control.status == "merged":
        raise Conflict("控制点已被合并", code="rcsa.control_merged", params={"control_id": control.id})
    if spec.coverage_disposition == CoverageDisposition.EXCLUDED and not spec.coverage_reason:
        raise Conflict("排除受影响控制需要写明理由", code="rcsa.reason_required")
    return RcsaTriggerControl(
        trigger_id=trigger_id, control_id=control.id,
        control_snapshot={"id": control.id, "code": control.code, "title": control.title,
                          "owner_user_id": control.owner_user_id},
        coverage_disposition=spec.coverage_disposition, coverage_reason=spec.coverage_reason,
    )


async def register(session: AsyncSession, payload: TriggerIn, actor: User) -> RcsaTrigger:
    await _owner(session, payload.owner_id)
    trigger = RcsaTrigger(
        **payload.model_dump(exclude={"control_ids"}), status=TriggerStatus.REGISTERED,
        created_by=actor.id, updated_by=actor.id,
    )
    session.add(trigger)
    await session.flush()
    for control_id in dict.fromkeys(payload.control_ids):
        session.add(await _control_row(session, trigger.id, TriggerControlIn(control_id=control_id)))
    await session.flush()
    await audit(session, actor, "trigger_register", "RcsaTrigger", trigger.id,
                {"trigger_type": payload.trigger_type, "owner_id": payload.owner_id,
                 "control_ids": list(dict.fromkeys(payload.control_ids))})
    return trigger


def _require_owner(trigger: RcsaTrigger, actor: User) -> None:
    if trigger.owner_id != actor.id:
        raise Forbidden("只有触发负责人能处置", code="rcsa.not_trigger_owner")


def _require_status(trigger: RcsaTrigger, *allowed: TriggerStatus) -> None:
    if trigger.status not in allowed:
        raise Conflict("触发当前状态不允许这个操作", code="rcsa.trigger_state",
                       params={"status": str(trigger.status)})


async def update(session: AsyncSession, trigger_id: int, payload: TriggerUpdateIn, actor: User) -> RcsaTrigger:
    trigger = await lock(session, RcsaTrigger, trigger_id, code="rcsa.trigger_not_found")
    if trigger.owner_id != actor.id:
        ensure(actor, Permission.RCSA_MANAGE)
    _require_status(trigger, TriggerStatus.REGISTERED, TriggerStatus.TRIAGED)
    check_version(trigger, payload.expected_version)
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version", "controls"})
    if "owner_id" in changes:
        await _owner(session, changes["owner_id"])
    for field, value in changes.items():
        setattr(trigger, field, value)
    if payload.controls is not None:
        for row in await session.scalars(select(RcsaTriggerControl).where(RcsaTriggerControl.trigger_id == trigger.id)):
            await session.delete(row)
        await session.flush()
        for spec in {spec.control_id: spec for spec in payload.controls}.values():
            session.add(await _control_row(session, trigger.id, spec))
    bump(trigger, actor)
    await session.flush()
    await audit(session, actor, "trigger_update", "RcsaTrigger", trigger.id,
                {"fields": sorted(changes) + (["controls"] if payload.controls is not None else []),
                 "version": trigger.version})
    return trigger


async def _controls(session: AsyncSession, trigger_id: int) -> list[RcsaTriggerControl]:
    return list(await session.scalars(
        select(RcsaTriggerControl).where(RcsaTriggerControl.trigger_id == trigger_id).order_by(RcsaTriggerControl.id)
    ))


async def triage(session: AsyncSession, trigger_id: int, expected_version: int, actor: User) -> RcsaTrigger:
    trigger = await lock(session, RcsaTrigger, trigger_id, code="rcsa.trigger_not_found")
    _require_owner(trigger, actor)
    _require_status(trigger, TriggerStatus.REGISTERED)
    check_version(trigger, expected_version)
    if not await _controls(session, trigger.id):
        raise Conflict("没有确认受影响的控制点，不能继续", code="rcsa.trigger_scope_empty")
    trigger.status = TriggerStatus.TRIAGED
    bump(trigger, actor)
    await session.flush()
    await audit(session, actor, "trigger_triage", "RcsaTrigger", trigger.id, {"status": "triaged"})
    return trigger


async def decide(session: AsyncSession, trigger_id: int, payload: TriggerDecideIn, actor: User) -> RcsaTrigger:
    trigger = await lock(session, RcsaTrigger, trigger_id, code="rcsa.trigger_not_found")
    _require_owner(trigger, actor)
    _require_status(trigger, TriggerStatus.TRIAGED)
    check_version(trigger, payload.expected_version)
    if payload.decision == TriggerDisposition.NO_REASSESSMENT:
        if not payload.rationale:
            raise Conflict("不重评需要书面理由", code="rcsa.reason_required")
        trigger.status = TriggerStatus.NO_REASSESSMENT
        trigger.proposed_by = actor.id
        trigger.proposed_at = _now()
    else:
        trigger.status = TriggerStatus.AWAITING_ASSESSMENT
    trigger.disposition = payload.decision
    trigger.rationale = payload.rationale
    bump(trigger, actor)
    await session.flush()
    await audit(session, actor, "trigger_decide", "RcsaTrigger", trigger.id,
                {"decision": payload.decision, "status": str(trigger.status)})
    return trigger


async def review(
    session: AsyncSession, trigger_id: int, payload: TriggerReviewIn, actor: User
) -> RcsaTrigger:
    trigger = await lock(session, RcsaTrigger, trigger_id, code="rcsa.trigger_not_found")
    _require_status(trigger, TriggerStatus.NO_REASSESSMENT)
    if trigger.proposed_by == actor.id:
        raise Forbidden("提议人不能确认自己的不重评决定", code="rcsa.approver_is_proposer")
    check_version(trigger, payload.expected_version)
    approve = payload.decision == "approve"
    if not approve and not payload.reason:
        raise Conflict("驳回需要写明理由", code="rcsa.reason_required")
    session.add(RcsaReview(
        trigger_id=trigger.id,
        decision=ReviewDecision.APPROVE_NO_REASSESSMENT if approve else ReviewDecision.REJECT_NO_REASSESSMENT,
        target_version=trigger.version, reviewer_id=actor.id, reason=payload.reason, at=_now(),
    ))
    if approve:
        trigger.status = TriggerStatus.COMPLETED
    else:
        # 驳回后回到分诊后的状态，由负责人重新决定；提议记录留在复核记录里。
        trigger.status = TriggerStatus.TRIAGED
        trigger.disposition = None
        trigger.proposed_by = None
        trigger.proposed_at = None
    bump(trigger, actor)
    await session.flush()
    await audit(session, actor, "trigger_review", "RcsaTrigger", trigger.id,
                {"decision": payload.decision, "status": str(trigger.status)})
    return trigger


async def link(session: AsyncSession, trigger_id: int, payload: TriggerLinkIn, actor: User) -> RcsaTrigger:
    trigger = await lock(session, RcsaTrigger, trigger_id, code="rcsa.trigger_not_found")
    if trigger.owner_id != actor.id:
        ensure(actor, Permission.RCSA_MANAGE)
    _require_status(trigger, TriggerStatus.AWAITING_ASSESSMENT)
    check_version(trigger, payload.expected_version)
    campaign = await fetch(session, RcsaCampaign, payload.campaign_id, code="rcsa.campaign_not_found")
    if campaign.status in (CampaignStatus.CLOSED, CampaignStatus.CANCELLED):
        raise Conflict("只能关联尚未关闭的活动", code="rcsa.campaign_state", params={"status": str(campaign.status)})
    in_campaign = set(await session.scalars(select(RcsaItem.control_id).where(RcsaItem.campaign_id == campaign.id)))
    excluded = {row.control_id: row.reason for row in payload.exclusions}
    rows = await _controls(session, trigger.id)
    uncovered = [
        row.control_snapshot.get("code") for row in rows
        if row.control_id not in in_campaign and row.control_id not in excluded
    ]
    if uncovered:
        raise Conflict("活动没有覆盖全部受影响控制，未覆盖的要逐项写排除理由",
                       code="rcsa.trigger_controls_uncovered", params={"control_codes": uncovered})
    for row in rows:
        if row.control_id in in_campaign:
            row.coverage_disposition, row.coverage_reason = CoverageDisposition.ASSESS, ""
        else:
            row.coverage_disposition, row.coverage_reason = CoverageDisposition.EXCLUDED, excluded[row.control_id]
    trigger.campaign_id = campaign.id
    bump(trigger, actor)
    await session.flush()
    await audit(session, actor, "trigger_link", "RcsaTrigger", trigger.id,
                {"campaign_id": campaign.id, "excluded_control_ids": sorted(excluded)})
    return trigger


async def complete_for_campaign(session: AsyncSession, campaign_id: int, actor: User) -> list[int]:
    """活动关闭的同一事务里，把关联它、等待重评的触发标为完成。"""
    triggers = list(await session.scalars(
        select(RcsaTrigger).where(
            RcsaTrigger.campaign_id == campaign_id, RcsaTrigger.status == TriggerStatus.AWAITING_ASSESSMENT
        ).with_for_update()
    ))
    for trigger in triggers:
        trigger.status = TriggerStatus.COMPLETED
        bump(trigger, actor)
        await audit(session, actor, "trigger_complete", "RcsaTrigger", trigger.id, {"campaign_id": campaign_id})
    await session.flush()
    return [trigger.id for trigger in triggers]
