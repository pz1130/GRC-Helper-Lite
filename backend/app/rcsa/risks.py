"""风险情景、处置、整改行动与发布到登记册（2C）。

分值一律由服务端按活动冻结的评分口径计算。批准之后再改评分或情景，该项回到草稿、
重新提交与复核——不能靠改风险表绕过复核。处置与行动在响应批准之后各自走批准与验证，
活动关闭后仍可跟进（它们是「当前跟进」，不改闭期快照）。

处置状态与风险状态分开：批准一个策略不会改动当前剩余评分，目标风险也不替代现状。
接受到期不自动续期，续期就是重新提出、重新批准。
"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, Forbidden, NotFound
from app.iam.models import User
from app.iam.permissions import Permission, has_permission
from app.rcsa.access import Command, audit, bump, check_version, ensure, fetch, lock
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
    RcsaReview,
    ResponseStatus,
    ReviewDecision,
)
from app.rcsa.responses import locked_item, new_revision, require_assignee
from app.rcsa.schemas import (
    ActionIn,
    ActionUpdateIn,
    DispositionIn,
    DispositionReviewIn,
    PublishIn,
    RiskIn,
    RiskUpdateIn,
    VerifyIn,
)
from app.risk.models import RiskEntry, RiskSource, RiskStatus

LIVE = (CampaignStatus.ACTIVE, CampaignStatus.IN_REVIEW)
FOLLOW_UP = (CampaignStatus.ACTIVE, CampaignStatus.IN_REVIEW, CampaignStatus.CLOSED, CampaignStatus.CANCELLED)
_SCORING = {
    "cause", "event", "impact", "inherent_likelihood", "inherent_impact", "inherent_rationale",
    "residual_likelihood", "residual_impact", "residual_rationale",
}


def _now() -> datetime:
    return datetime.now(UTC)


def score(campaign: RcsaCampaign, likelihood: int, impact: int) -> int:
    algorithm = (campaign.profile_snapshot or {}).get("algorithm", "product")
    if algorithm != "product":
        raise Conflict("评分算法未实现", code="rcsa.algorithm_unsupported", params={"algorithm": algorithm})
    return likelihood * impact


async def _active_user(session: AsyncSession, user_id: int, perm: Permission) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("用户不存在", code="rcsa.user_not_found", params={"user_id": user_id})
    if not user.is_active or not has_permission(user.role, perm) or user.engagement_scope_id:
        raise Conflict(
            "这个人没有对应权限或已停用",
            code="rcsa.person_unavailable",
            params={"user_id": user_id, "permission": perm.value},
        )
    return user


async def _locked_risk(
    session: AsyncSession, risk_id: int, allowed: tuple[CampaignStatus, ...]
) -> tuple[RcsaCampaign, RcsaItem, RcsaItemRisk]:
    risk = await fetch(session, RcsaItemRisk, risk_id, code="rcsa.risk_not_found")
    campaign, item = await locked_item(session, risk.item_id, allowed)
    risk = await lock(session, RcsaItemRisk, risk_id, code="rcsa.risk_not_found")
    return campaign, item, risk


async def _locked_action(
    session: AsyncSession, action_id: int, allowed: tuple[CampaignStatus, ...]
) -> tuple[RcsaItemRisk, RcsaAction]:
    action = await fetch(session, RcsaAction, action_id, code="rcsa.action_not_found")
    _, _, risk = await _locked_risk(session, action.item_risk_id, allowed)
    action = await lock(session, RcsaAction, action_id, code="rcsa.action_not_found")
    return risk, action


def _require_item_approved(item: RcsaItem) -> None:
    if item.status != ItemStatus.APPROVED:
        raise Conflict("评估项的回答还没有复核通过", code="rcsa.item_not_approved")


# ── 情景 ────────────────────────────────────────────────────


async def list_risks(session: AsyncSession, item_id: int) -> list[RcsaItemRisk]:
    await fetch(session, RcsaItem, item_id, code="rcsa.item_not_found")
    return list(
        await session.scalars(select(RcsaItemRisk).where(RcsaItemRisk.item_id == item_id).order_by(RcsaItemRisk.id))
    )


def _editable(item: RcsaItem) -> None:
    if item.status == ItemStatus.SUBMITTED:
        raise Conflict("回答正在复核中，情景与评分暂时不能改", code="rcsa.item_under_review")
    if item.status == ItemStatus.EXCLUDED:
        raise Conflict("评估项已排除", code="rcsa.item_state", params={"status": str(item.status)})


async def add_risk(session: AsyncSession, item_id: int, payload: RiskIn, actor: User) -> RcsaItemRisk:
    campaign, item = await locked_item(session, item_id)
    require_assignee(item, actor)
    _editable(item)
    await _active_user(session, payload.owner_id, Permission.READ)
    exists = await session.scalar(
        select(func.count()).select_from(RcsaItemRisk).where(
            RcsaItemRisk.item_id == item.id, RcsaItemRisk.scenario_key == payload.scenario_key
        )
    )
    if exists:
        raise Conflict("这个情景键已存在", code="rcsa.scenario_exists")
    data = payload.model_dump()
    risk = RcsaItemRisk(
        **data,
        item_id=item.id,
        inherent_score=score(campaign, payload.inherent_likelihood, payload.inherent_impact),
        residual_score=(
            score(campaign, payload.residual_likelihood, payload.residual_impact)
            if payload.residual_likelihood is not None else None
        ),
        created_by=actor.id,
        updated_by=actor.id,
    )
    session.add(risk)
    await session.flush()
    await _reopen_if_approved(session, item, actor)
    await audit(session, actor, "risk_add", "RcsaItemRisk", risk.id,
                {"item_id": item.id, "scenario_key": risk.scenario_key})
    return risk


async def _reopen_if_approved(session: AsyncSession, item: RcsaItem, actor: User) -> None:
    """批准后改评分或情景：该项回到草稿，下一版从已批准的回答起步，需重新提交与复核。"""
    if item.status == ItemStatus.APPROVED:
        await new_revision(session, item, actor)


async def update_risk(session: AsyncSession, risk_id: int, payload: RiskUpdateIn, actor: User) -> RcsaItemRisk:
    campaign, item, risk = await _locked_risk(session, risk_id, (CampaignStatus.ACTIVE,))
    require_assignee(item, actor)
    _editable(item)
    check_version(risk, payload.expected_version)
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    for field, value in changes.items():
        setattr(risk, field, value)
    risk.inherent_score = score(campaign, risk.inherent_likelihood, risk.inherent_impact)
    risk.residual_score = (
        score(campaign, risk.residual_likelihood, risk.residual_impact)
        if risk.residual_likelihood is not None else None
    )
    bump(risk, actor)
    await session.flush()
    if changes.keys() & _SCORING:
        await _reopen_if_approved(session, item, actor)
    await audit(session, actor, "risk_update", "RcsaItemRisk", risk.id,
                {"fields": sorted(changes), "version": risk.version, "item_status": str(item.status)})
    return risk


async def reassign_risk_owner(
    session: AsyncSession, risk_id: int, owner_id: int, expected_version: int, actor: User
) -> RcsaItemRisk:
    _, _, risk = await _locked_risk(session, risk_id, FOLLOW_UP)
    check_version(risk, expected_version)
    await _active_user(session, owner_id, Permission.READ)
    before = risk.owner_id
    risk.owner_id = owner_id
    bump(risk, actor)
    await session.flush()
    await audit(session, actor, "risk_owner", "RcsaItemRisk", risk.id,
                {"owner_id": owner_id, "version": risk.version}, before={"owner_id": before})
    return risk


async def remove_risk(session: AsyncSession, risk_id: int, actor: User) -> None:
    _, item, risk = await _locked_risk(session, risk_id, (CampaignStatus.ACTIVE,))
    require_assignee(item, actor)
    if item.status not in (ItemStatus.PENDING, ItemStatus.DRAFT, ItemStatus.RETURNED):
        raise Conflict("评估项当前状态不能删除情景", code="rcsa.item_state", params={"status": str(item.status)})
    linked = await session.scalar(select(func.count()).select_from(RcsaAction).where(RcsaAction.item_risk_id == risk.id))
    if linked or risk.register_risk_id or risk.disposition_status != DispositionStatus.PENDING:
        raise Conflict("这个情景已有处置、行动或登记册关联，不能删除", code="rcsa.risk_in_use")
    await session.delete(risk)
    await session.flush()
    await audit(session, actor, "risk_remove", "RcsaItem", item.id, {"risk_id": risk_id})


# ── 处置 ────────────────────────────────────────────────────


async def _live_actions(session: AsyncSession, risk_id: int, *, excluding: int | None = None) -> int:
    statement = select(func.count()).select_from(RcsaAction).where(
        RcsaAction.item_risk_id == risk_id, RcsaAction.status != ActionStatus.CANCELLED
    )
    if excluding is not None:
        statement = statement.where(RcsaAction.id != excluding)
    return int(await session.scalar(statement) or 0)


def _needs_action(disposition: str | None, details: dict[str, Any]) -> bool:
    """reduce 永远要行动；未生效的 transfer/avoid/terminate 也要行动或里程碑。"""
    if disposition == Disposition.REDUCE:
        return True
    return disposition in (Disposition.TRANSFER, Disposition.AVOID, Disposition.TERMINATE) and not details.get(
        "effective"
    )


async def _disposition_problems(
    session: AsyncSession, risk: RcsaItemRisk, disposition: str, details: dict[str, Any],
    accept_until: date | None, reassess_at: date | None,
) -> list[str]:
    problems: list[str] = []

    def problem(code: str) -> None:
        problems.append(code)

    def given(*keys: str) -> bool:
        return all(str(details.get(key, "")).strip() for key in keys)

    today = _now().date()
    if disposition == Disposition.ACCEPT:
        if not given("rationale"):
            problem(code="rcsa.problem.accept_rationale")
        if not given("conditions"):
            problem(code="rcsa.problem.accept_conditions")
        if accept_until is None or accept_until <= today:
            problem(code="rcsa.problem.accept_until")
        if reassess_at is None or (accept_until is not None and reassess_at > accept_until):
            problem(code="rcsa.problem.reassess_at")
    elif disposition == Disposition.TRANSFER and not given("counterparty", "basis_ref", "retained_risk"):
        problem(code="rcsa.problem.transfer_details")
    elif disposition == Disposition.AVOID and not given("decision", "scope", "responsible", "basis_ref"):
        problem(code="rcsa.problem.avoid_details")
    elif disposition == Disposition.TERMINATE and not given(
        "scope", "completion_criteria", "responsible", "basis_ref"
    ):
        problem(code="rcsa.problem.terminate_details")
    if _needs_action(disposition, details) and not await _live_actions(session, risk.id):
        if disposition == Disposition.REDUCE:
            problem(code="rcsa.problem.reduce_action")
        else:
            problem(code="rcsa.problem.milestone_action")
    return problems


async def propose(session: AsyncSession, risk_id: int, payload: DispositionIn, actor: User) -> RcsaItemRisk:
    _, item, risk = await _locked_risk(session, risk_id, LIVE)
    if actor.id not in (item.assignee_id, risk.owner_id):
        raise Forbidden("只有作答人或风险责任人能提出处置", code="rcsa.not_risk_proposer")
    _require_item_approved(item)
    if risk.disposition_status == DispositionStatus.PROPOSED:
        raise Conflict("已有一个处置在等待批准", code="rcsa.disposition_pending")
    check_version(risk, payload.expected_version)
    problems = await _disposition_problems(
        session, risk, payload.disposition, payload.details, payload.accept_until, payload.reassess_at
    )
    if problems:
        raise Conflict("处置信息不完整", code="rcsa.disposition_incomplete", params={"problems": problems})
    risk.disposition = payload.disposition
    risk.disposition_details = payload.details
    risk.accept_until = payload.accept_until
    risk.reassess_at = payload.reassess_at
    risk.disposition_status = DispositionStatus.PROPOSED
    risk.disposition_revision += 1
    risk.disposition_proposed_by = actor.id
    risk.disposition_proposed_at = _now()
    bump(risk, actor)
    await session.flush()
    await audit(session, actor, "disposition", "RcsaItemRisk", risk.id,
                {"disposition": payload.disposition, "disposition_revision": risk.disposition_revision})
    return risk


async def review_disposition(
    session: AsyncSession, risk_id: int, payload: DispositionReviewIn, actor: User, client_key: str
) -> RcsaItemRisk:
    _, _, risk = await _locked_risk(session, risk_id, LIVE)
    command = Command(actor, "review_disposition", "RcsaItemRisk", risk.id, client_key, payload.model_dump())
    if await command.replay(session) is not None:
        return risk
    if risk.disposition_status != DispositionStatus.PROPOSED:
        raise Conflict("没有等待批准的处置", code="rcsa.disposition_not_proposed")
    # 职责分离对管理员同样适用：提议人不能批准自己的处置。
    if risk.disposition_proposed_by == actor.id:
        raise Forbidden("提议人不能批准自己的处置", code="rcsa.approver_is_proposer")
    check_version(risk, payload.expected_version)
    approve = payload.decision == "approve"
    if not approve and not payload.reason:
        raise Conflict("退回需要写明理由", code="rcsa.reason_required")
    if approve:
        problems = await _disposition_problems(
            session, risk, risk.disposition, risk.disposition_details, risk.accept_until, risk.reassess_at
        )
        if problems:
            raise Conflict("处置信息不完整", code="rcsa.disposition_incomplete", params={"problems": problems})
    risk.disposition_status = DispositionStatus.APPROVED if approve else DispositionStatus.RETURNED
    bump(risk, actor)
    session.add(RcsaReview(
        item_risk_id=risk.id,
        decision=ReviewDecision.APPROVE_DISPOSITION if approve else ReviewDecision.RETURN_DISPOSITION,
        target_version=risk.disposition_revision, reviewer_id=actor.id, reason=payload.reason, at=_now(),
    ))
    await session.flush()
    await command.done(session, risk.id, {"decision": payload.decision, "disposition": str(risk.disposition),
                                          "disposition_revision": risk.disposition_revision,
                                          "accept_until": risk.accept_until.isoformat() if risk.accept_until else None})
    return risk


async def risk_reviews(session: AsyncSession, risk_id: int) -> list[RcsaReview]:
    await fetch(session, RcsaItemRisk, risk_id, code="rcsa.risk_not_found")
    action_ids = select(RcsaAction.id).where(RcsaAction.item_risk_id == risk_id)
    return list(
        await session.scalars(
            select(RcsaReview)
            .where((RcsaReview.item_risk_id == risk_id) | RcsaReview.action_id.in_(action_ids))
            .order_by(RcsaReview.id)
        )
    )


# ── 登记册 ──────────────────────────────────────────────────


async def publish(
    session: AsyncSession, risk_id: int, payload: PublishIn, actor: User, client_key: str
) -> RcsaItemRisk:
    """显式发布：只创建或关联，不双向同步，也不改写已有登记册风险的评分。"""
    campaign, item, risk = await _locked_risk(
        session, risk_id, (CampaignStatus.ACTIVE, CampaignStatus.IN_REVIEW, CampaignStatus.CLOSED)
    )
    command = Command(actor, "publish_risk", "RcsaItemRisk", risk.id, client_key, payload.model_dump())
    if await command.replay(session) is not None or risk.register_risk_id is not None:
        return risk
    _require_item_approved(item)
    check_version(risk, payload.expected_version)
    response = await session.get(RcsaResponse, item.current_response_id)
    if response is None or response.status != ResponseStatus.APPROVED:
        raise Conflict("评估项的回答还没有复核通过", code="rcsa.item_not_approved")
    if payload.mode == "link":
        if payload.register_risk_id is None:
            raise Conflict("关联已有风险需要给出登记册风险", code="rcsa.register_risk_required")
        entry = await fetch(session, RiskEntry, payload.register_risk_id, code="rcsa.register_risk_not_found")
    else:
        code = (item.control_snapshot or {}).get("code", "")
        implementation = item.implementation_snapshot
        if implementation:
            asset = implementation.get("asset")
            code += f" · {asset['name']}" if asset else " · Process implementation"
        entry = RiskEntry(
            title=f"{code}: {risk.event}"[:300],
            description=f"Cause: {risk.cause}\nEvent: {risk.event}\nImpact: {risk.impact}",
            source=RiskSource.RCSA,
            source_ref={
                "rcsa_item_risk_id": risk.id, "campaign_id": campaign.id, "item_id": item.id,
                "response_id": response.id, "response_revision": response.revision,
                "scenario_key": risk.scenario_key,
                **({"implementation_id": item.implementation_id} if item.implementation_id is not None else {}),
            },
            control_id=item.control_id,
            likelihood=risk.inherent_likelihood,
            impact=risk.inherent_impact,
            inherent_score=risk.inherent_score,
            residual_likelihood=risk.residual_likelihood,
            residual_impact=risk.residual_impact,
            residual_score=risk.residual_score,
            owner_user_id=risk.owner_id,
            status=RiskStatus.OPEN,
        )
        session.add(entry)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise Conflict("这个情景已经发布过", code="rcsa.already_published") from exc
    risk.register_risk_id = entry.id
    bump(risk, actor)
    await session.flush()
    await command.done(session, risk.id, {"register_risk_id": entry.id, "mode": payload.mode,
                                          "response_revision": response.revision})
    return risk


# ── 行动 ────────────────────────────────────────────────────


async def list_actions(session: AsyncSession, risk_id: int) -> list[RcsaAction]:
    await fetch(session, RcsaItemRisk, risk_id, code="rcsa.risk_not_found")
    return list(
        await session.scalars(select(RcsaAction).where(RcsaAction.item_risk_id == risk_id).order_by(RcsaAction.id))
    )


async def _check_people(session: AsyncSession, owner_id: int, verifier_id: int) -> None:
    if owner_id == verifier_id:
        raise Conflict("验证人不能是行动负责人", code="rcsa.verifier_is_owner")
    await _active_user(session, owner_id, Permission.RCSA_ACTION_UPDATE)
    await _active_user(session, verifier_id, Permission.RCSA_REVIEW)


async def add_action(session: AsyncSession, risk_id: int, payload: ActionIn, actor: User) -> RcsaAction:
    _, item, risk = await _locked_risk(session, risk_id, LIVE)
    if actor.id not in (item.assignee_id, risk.owner_id) and not has_permission(actor.role, Permission.RCSA_MANAGE):
        raise Forbidden("只有作答人、风险责任人或活动管理者能新增行动", code="rcsa.not_risk_proposer")
    await _check_people(session, payload.owner_id, payload.verifier_id)
    action = RcsaAction(item_risk_id=risk.id, status=ActionStatus.OPEN, **payload.model_dump(),
                        created_by=actor.id, updated_by=actor.id)
    session.add(action)
    await session.flush()
    await audit(session, actor, "action_add", "RcsaAction", action.id,
                {"item_risk_id": risk.id, "owner_id": action.owner_id, "verifier_id": action.verifier_id,
                 "due_at": action.due_at.isoformat()})
    return action


def _require_open(action: RcsaAction) -> None:
    if action.status in (ActionStatus.VERIFIED, ActionStatus.CANCELLED):
        raise Conflict("行动已结束", code="rcsa.action_closed")


async def update_action(session: AsyncSession, action_id: int, payload: ActionUpdateIn, actor: User) -> RcsaAction:
    _, action = await _locked_action(session, action_id, FOLLOW_UP)
    _require_open(action)
    check_version(action, payload.expected_version)
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version", "reason"})
    reassigning = changes.keys() & {"owner_id", "verifier_id", "due_at"}
    if reassigning:
        ensure(actor, Permission.RCSA_MANAGE)
        if not payload.reason:
            raise Conflict("改负责人、验证人或期限需要写明理由", code="rcsa.reason_required")
        await _check_people(session, changes.get("owner_id", action.owner_id),
                            changes.get("verifier_id", action.verifier_id))
    if changes.keys() - {"owner_id", "verifier_id", "due_at"} and actor.id != action.owner_id:
        raise Forbidden("只有行动负责人能更新进度", code="rcsa.not_action_owner")
    if changes.get("status") == ActionStatus.PENDING_VERIFICATION and not changes.get(
        "implementation_evidence", action.implementation_evidence
    ):
        raise Conflict("提交验证前要登记实施证据", code="rcsa.action_evidence_required")
    before = {field: str(getattr(action, field)) for field in sorted(reassigning)}
    for field, value in changes.items():
        setattr(action, field, value)
    bump(action, actor)
    await session.flush()
    await audit(session, actor, "action_update", "RcsaAction", action.id,
                {"fields": sorted(changes), "status": str(action.status), "reason": payload.reason,
                 "version": action.version}, before=before or None)
    return action


async def verify(
    session: AsyncSession, action_id: int, payload: VerifyIn, actor: User, client_key: str
) -> RcsaAction:
    _, action = await _locked_action(session, action_id, FOLLOW_UP)
    command = Command(actor, "action_verify", "RcsaAction", action.id, client_key, payload.model_dump())
    if await command.replay(session) is not None:
        return action
    if action.verifier_id != actor.id or action.owner_id == actor.id:
        raise Forbidden("只有指定的验证人能验证，且不能是实施者", code="rcsa.not_verifier")
    if action.status != ActionStatus.PENDING_VERIFICATION:
        raise Conflict("行动还没有提交验证", code="rcsa.action_state", params={"status": str(action.status)})
    check_version(action, payload.expected_version)
    passed = payload.decision == "pass"
    action.status = ActionStatus.VERIFIED if passed else ActionStatus.IN_PROGRESS
    action.verification_result = payload.result
    action.verified_at = _now() if passed else None
    bump(action, actor)
    session.add(RcsaReview(
        action_id=action.id, decision=ReviewDecision.VERIFY_PASS if passed else ReviewDecision.VERIFY_FAIL,
        target_version=action.version, reviewer_id=actor.id, reason=payload.result, at=_now(),
    ))
    await session.flush()
    await command.done(session, action.id, {"decision": payload.decision, "status": str(action.status)})
    return action


async def cancel_action(
    session: AsyncSession, action_id: int, reason: str, expected_version: int, actor: User
) -> RcsaAction:
    risk, action = await _locked_action(session, action_id, FOLLOW_UP)
    _require_open(action)
    if actor.id == action.owner_id:
        raise Forbidden("行动负责人不能自己取消行动", code="rcsa.cancel_by_owner")
    check_version(action, expected_version)
    if (
        risk.disposition_status in (DispositionStatus.PROPOSED, DispositionStatus.APPROVED)
        and _needs_action(risk.disposition, risk.disposition_details)
        and not await _live_actions(session, risk.id, excluding=action.id)
    ):
        raise Conflict("取消后这个处置就没有行动了；先改处置或补一条行动", code="rcsa.disposition_needs_action")
    action.status = ActionStatus.CANCELLED
    bump(action, actor)
    session.add(RcsaReview(action_id=action.id, decision=ReviewDecision.APPROVE_CANCELLATION,
                           target_version=action.version, reviewer_id=actor.id, reason=reason, at=_now()))
    await session.flush()
    await audit(session, actor, "action_cancel", "RcsaAction", action.id, {"reason": reason})
    return action
