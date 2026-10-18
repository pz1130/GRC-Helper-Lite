"""作答、证据、提交、复核与排除（2B）。

每次提交生成不可变的 revision，复核绑定具体 revision。退回后新建草稿拷贝旧回答，
旧版本原样保留。批准表示「复核同意这份评估记录」，不是「控制有效」。

证据只是引用：服务端不抓取外部链接，也不保存带凭证的链接。证据快照在提交那一刻
按当时的登记信息刷新一次，之后证据本身怎么变都不改写这份记录。
"""

import re
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError, Conflict, Forbidden, NotFound
from app.evidence.models import EvidenceItem, EvidenceType
from app.iam.models import User
from app.rcsa.access import Command, audit, bump, check_version, fetch, lock
from app.rcsa.models import (
    CampaignStatus,
    ItemStatus,
    RcsaCampaign,
    RcsaItem,
    RcsaItemRisk,
    RcsaResponse,
    RcsaResponseEvidence,
    RcsaReview,
    ResponseStatus,
    ReviewDecision,
)
from app.rcsa.schemas import AnswerV1, EvidenceIn, EvidenceOut, ResponseOut, ReviewIn


def _now() -> datetime:
    return datetime.now(UTC)


def _today() -> date:
    return _now().date()


# ── 读 ──────────────────────────────────────────────────────


async def response_out(session: AsyncSession, response: RcsaResponse) -> ResponseOut:
    rows = await session.scalars(
        select(RcsaResponseEvidence)
        .where(RcsaResponseEvidence.response_id == response.id)
        .order_by(RcsaResponseEvidence.id)
    )
    out = ResponseOut.model_validate(response)
    out.evidence = [EvidenceOut.model_validate(row) for row in rows]
    return out


async def history(session: AsyncSession, item_id: int) -> list[RcsaResponse]:
    await fetch(session, RcsaItem, item_id, code="rcsa.item_not_found")
    return list(
        await session.scalars(
            select(RcsaResponse).where(RcsaResponse.item_id == item_id).order_by(RcsaResponse.revision)
        )
    )


async def item_reviews(session: AsyncSession, item_id: int) -> list[RcsaReview]:
    await fetch(session, RcsaItem, item_id, code="rcsa.item_not_found")
    response_ids = select(RcsaResponse.id).where(RcsaResponse.item_id == item_id)
    return list(
        await session.scalars(
            select(RcsaReview)
            .where((RcsaReview.item_id == item_id) | RcsaReview.response_id.in_(response_ids))
            .order_by(RcsaReview.id)
        )
    )


# ── 锁与身份 ────────────────────────────────────────────────


async def locked_item(
    session: AsyncSession, item_id: int, allowed: tuple[CampaignStatus, ...] = (CampaignStatus.ACTIVE,)
) -> tuple[RcsaCampaign, RcsaItem]:
    """子项写入：活动 FOR SHARE、评估项 FOR UPDATE，并要求活动处在允许的状态。"""
    item = await fetch(session, RcsaItem, item_id, code="rcsa.item_not_found")
    campaign = await lock(session, RcsaCampaign, item.campaign_id, code="rcsa.campaign_not_found", share=True)
    if campaign.status not in allowed:
        raise Conflict(
            "活动当前状态不允许这个操作",
            code="rcsa.campaign_state",
            params={"status": str(campaign.status)},
        )
    item = await lock(session, RcsaItem, item_id, code="rcsa.item_not_found")
    return campaign, item


async def _locked_response(
    session: AsyncSession, response_id: int
) -> tuple[RcsaCampaign, RcsaItem, RcsaResponse]:
    response = await fetch(session, RcsaResponse, response_id, code="rcsa.response_not_found")
    campaign, item = await locked_item(session, response.item_id)
    response = await lock(session, RcsaResponse, response_id, code="rcsa.response_not_found")
    return campaign, item, response


def require_assignee(item: RcsaItem, actor: User) -> None:
    if item.assignee_id != actor.id:
        raise Forbidden("只有指派的作答人能作答", code="rcsa.not_assignee")


def _require_reviewer(item: RcsaItem, actor: User) -> None:
    if item.reviewer_id != actor.id:
        raise Forbidden("只有指派的复核人能执行这个操作", code="rcsa.not_reviewer")


def _require_draft(response: RcsaResponse) -> None:
    if response.status != ResponseStatus.DRAFT:
        raise Conflict("已提交的版本不能修改", code="rcsa.response_not_draft")


# ── 草稿 ────────────────────────────────────────────────────


async def open_draft(session: AsyncSession, item_id: int, expected_version: int, actor: User) -> RcsaResponse:
    _, item = await locked_item(session, item_id)
    require_assignee(item, actor)
    check_version(item, expected_version)
    if item.status == ItemStatus.DRAFT:
        raise Conflict("这个评估项已经有一份草稿", code="rcsa.draft_exists")
    if item.status not in (ItemStatus.PENDING, ItemStatus.RETURNED):
        raise Conflict("评估项当前状态不能新建草稿", code="rcsa.item_state", params={"status": str(item.status)})
    response = await new_revision(session, item, actor)
    await audit(session, actor, "response_open", "RcsaResponse", response.id,
                {"item_id": item.id, "revision": response.revision})
    return response


async def new_revision(session: AsyncSession, item: RcsaItem, actor: User) -> RcsaResponse:
    """在评估项上开下一版草稿。调用方已锁住评估项并检查过状态。"""
    previous = await session.scalar(
        select(RcsaResponse).where(RcsaResponse.item_id == item.id).order_by(RcsaResponse.revision.desc()).limit(1)
    )
    response = RcsaResponse(
        item_id=item.id,
        revision=(previous.revision + 1) if previous else 1,
        status=ResponseStatus.DRAFT,
        # 退回后的新版本从上一版回答起步，旧版本不动。
        answer=dict(previous.answer) if previous else AnswerV1().model_dump(mode="json"),
        created_by=actor.id,
        updated_by=actor.id,
    )
    session.add(response)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise Conflict("这个评估项已经有一份草稿", code="rcsa.draft_exists") from exc
    if previous is not None:
        for evidence in await session.scalars(
            select(RcsaResponseEvidence).where(RcsaResponseEvidence.response_id == previous.id)
        ):
            session.add(_copy_evidence(evidence, response.id, actor))
    item.current_response_id = response.id
    item.status = ItemStatus.DRAFT
    bump(item, actor)
    await session.flush()
    return response


def _copy_evidence(evidence: RcsaResponseEvidence, response_id: int, actor: User) -> RcsaResponseEvidence:
    fields = {
        column: getattr(evidence, column)
        for column in (
            "evidence_item_id", "external_location", "purpose", "snapshot", "tester_user_id",
            "tester_name", "tested_at", "test_scope", "conclusion", "independence_basis",
        )
    }
    return RcsaResponseEvidence(response_id=response_id, created_by=actor.id, **fields)


async def save_answer(
    session: AsyncSession, response_id: int, answer: AnswerV1, expected_version: int, actor: User
) -> RcsaResponse:
    _, item, response = await _locked_response(session, response_id)
    require_assignee(item, actor)
    _require_draft(response)
    check_version(response, expected_version)
    response.answer = answer.model_dump(mode="json")
    bump(response, actor)
    await session.flush()
    await audit(session, actor, "response_save", "RcsaResponse", response.id,
                {"revision": response.revision, "version": response.version})
    return response


# ── 证据 ────────────────────────────────────────────────────

_SECRET_PARAMS = re.compile(
    r"^(token|access_token|id_token|auth|key|apikey|api_key|sig|signature|password|passwd|secret|sas|code)$",
    re.IGNORECASE,
)


def _carries_credentials(location: str) -> bool:
    parts = urlsplit(location.strip())
    if parts.username or parts.password:
        return True
    return any(_SECRET_PARAMS.match(name) for name, _ in parse_qsl(parts.query, keep_blank_values=True))


async def attach_evidence(
    session: AsyncSession, response_id: int, payload: EvidenceIn, actor: User
) -> RcsaResponseEvidence:
    _, item, response = await _locked_response(session, response_id)
    require_assignee(item, actor)
    _require_draft(response)
    if payload.external_location and _carries_credentials(payload.external_location):
        raise AppError(
            "链接里带有凭证或访问令牌；请给出不含凭证的位置",
            code="rcsa.evidence_location_has_credentials",
        )
    if payload.purpose == "independent_test" and payload.tester_user_id is not None:
        tester = await session.get(User, payload.tester_user_id)
        if tester is None:
            raise NotFound("测试人不存在", code="rcsa.user_not_found", params={"user_id": payload.tester_user_id})
        control_owner = (item.control_snapshot or {}).get("owner_user_id")
        if tester.id in (item.assignee_id, control_owner):
            raise Conflict(
                "测试人不能是本项作答人或控制执行负责人；请作为自评证据登记",
                code="rcsa.tester_not_independent",
            )
    snapshot: dict[str, Any] = {"coverage_note": payload.coverage_note}
    if payload.evidence_item_id is not None:
        evidence = await fetch(session, EvidenceItem, payload.evidence_item_id, code="rcsa.evidence_item_not_found")
        snapshot |= await _registered_snapshot(session, evidence)
    if payload.external_location:
        snapshot |= {
            "location": payload.external_location,
            "collected_at": payload.collected_at.isoformat() if payload.collected_at else None,
            "valid_until": payload.valid_until.isoformat() if payload.valid_until else None,
        }
    row = RcsaResponseEvidence(
        response_id=response.id,
        evidence_item_id=payload.evidence_item_id,
        external_location=payload.external_location or None,
        purpose=payload.purpose,
        snapshot=snapshot,
        tester_user_id=payload.tester_user_id,
        tester_name=payload.tester_name,
        tested_at=payload.tested_at,
        test_scope=payload.test_scope,
        conclusion=payload.conclusion,
        independence_basis=payload.independence_basis,
        created_by=actor.id,
    )
    session.add(row)
    bump(response, actor)
    await session.flush()
    await audit(session, actor, "evidence_attach", "RcsaResponse", response.id,
                {"evidence_id": row.id, "purpose": payload.purpose, "evidence_item_id": payload.evidence_item_id})
    return row


async def _registered_snapshot(session: AsyncSession, evidence: EvidenceItem) -> dict[str, Any]:
    evidence_type = await session.get(EvidenceType, evidence.evidence_type_id)
    return {
        "title": evidence.title,
        "evidence_type": evidence_type.name_en if evidence_type else None,
        "location": evidence.location_hint,
        "status": str(evidence.status),
        "control_id": evidence.control_id,
        "collected_at": evidence.last_collected_at.date().isoformat() if evidence.last_collected_at else None,
        "valid_until": evidence.valid_until.date().isoformat() if evidence.valid_until else None,
        "captured_at": _now().isoformat(),
    }


async def detach_evidence(session: AsyncSession, response_id: int, evidence_id: int, actor: User) -> None:
    _, item, response = await _locked_response(session, response_id)
    require_assignee(item, actor)
    _require_draft(response)
    row = await session.get(RcsaResponseEvidence, evidence_id)
    if row is None or row.response_id != response.id:
        raise NotFound("证据引用不存在", code="rcsa.evidence_not_found")
    await session.delete(row)
    bump(response, actor)
    await session.flush()
    await audit(session, actor, "evidence_detach", "RcsaResponse", response.id, {"evidence_id": evidence_id})


# ── 提交 ────────────────────────────────────────────────────


def risk_view(risk: RcsaItemRisk) -> dict[str, Any]:
    """随提交复制进 risk_snapshot 的风险情景。"""
    return {
        "id": risk.id, "scenario_key": risk.scenario_key, "cause": risk.cause, "event": risk.event,
        "impact": risk.impact, "inherent_likelihood": risk.inherent_likelihood,
        "inherent_impact": risk.inherent_impact, "inherent_score": risk.inherent_score,
        "inherent_rationale": risk.inherent_rationale, "residual_likelihood": risk.residual_likelihood,
        "residual_impact": risk.residual_impact, "residual_score": risk.residual_score,
        "residual_rationale": risk.residual_rationale, "owner_id": risk.owner_id,
    }


def _evidence_flagged(snapshot: dict[str, Any], period_start: date, today: date) -> bool:
    """已知日期显示过期，或收集早于评估期开始；没有日期的外部引用不算。"""
    valid_until = snapshot.get("valid_until")
    collected_at = snapshot.get("collected_at")
    if valid_until and date.fromisoformat(valid_until) < today:
        return True
    return bool(collected_at and date.fromisoformat(collected_at) < period_start)


async def _problems(
    session: AsyncSession, campaign: RcsaCampaign, response: RcsaResponse,
    evidence: list[RcsaResponseEvidence], risks: list[RcsaItemRisk],
) -> list[str]:
    """缺什么就列什么。每条是一个字面量 code（前端按 code 查词条）。"""
    answer = AnswerV1.model_validate(response.answer)
    problems: list[str] = []

    def problem(code: str) -> None:
        problems.append(code)

    if answer.design.rating is None:
        problem(code="rcsa.problem.design_rating")
    if not answer.design.rationale:
        problem(code="rcsa.problem.design_rationale")
    if answer.operating.rating is None:
        problem(code="rcsa.problem.operating_rating")
    if not answer.operating.rationale:
        problem(code="rcsa.problem.operating_rationale")
    if "not_assessed" in (answer.design.rating, answer.operating.rating):
        if not answer.not_assessed_reason:
            problem(code="rcsa.problem.not_assessed_reason")
        followup_owner = (
            await session.get(User, answer.evidence_followup.owner_id) if answer.evidence_followup else None
        )
        if followup_owner is None or not followup_owner.is_active:
            problem(code="rcsa.problem.evidence_followup")
    start, end = answer.assessment_period_start, answer.assessment_period_end
    if start is None or end is None or start > end:
        problem(code="rcsa.problem.assessment_period")
    if answer.independent_test_status is None:
        problem(code="rcsa.problem.independent_test_status")
    elif answer.independent_test_status == "provided":
        if not any(row.purpose == "independent_test" for row in evidence):
            problem(code="rcsa.problem.independent_test_evidence")
    elif not answer.independent_test_reason:
        problem(code="rcsa.problem.independent_test_reason")
    if not risks and not answer.no_risk_rationale:
        problem(code="rcsa.problem.risks_or_rationale")
    if any(risk.residual_likelihood is None for risk in risks):
        problem(code="rcsa.problem.risk_residual")
    if any(not risk.inherent_rationale or not risk.residual_rationale for risk in risks):
        problem(code="rcsa.problem.risk_rationale")
    today = _today()
    if any(_evidence_flagged(row.snapshot, campaign.period_start, today) for row in evidence) and not (
        answer.evidence_limitations_acknowledged and answer.limitations
    ):
        problem(code="rcsa.problem.evidence_limitations")
    return problems


async def submit(
    session: AsyncSession, response_id: int, expected_version: int, actor: User, client_key: str
) -> RcsaResponse:
    campaign, item, response = await _locked_response(session, response_id)
    command = Command(actor, "submit", "RcsaResponse", response.id, client_key,
                      {"expected_version": expected_version})
    if await command.replay(session) is not None:
        return response
    require_assignee(item, actor)
    _require_draft(response)
    check_version(response, expected_version)
    evidence = list(
        await session.scalars(
            select(RcsaResponseEvidence).where(RcsaResponseEvidence.response_id == response.id)
            .order_by(RcsaResponseEvidence.id)
        )
    )
    for row in evidence:
        if row.evidence_item_id is not None:
            current = await session.get(EvidenceItem, row.evidence_item_id)
            row.snapshot = row.snapshot | await _registered_snapshot(session, current)
    await session.flush()
    risks = list(
        await session.scalars(
            select(RcsaItemRisk).where(RcsaItemRisk.item_id == item.id).order_by(RcsaItemRisk.id)
        )
    )
    problems = await _problems(session, campaign, response, evidence, risks)
    if problems:
        raise Conflict("这份评估还不完整，不能提交", code="rcsa.submission_incomplete",
                       params={"problems": problems})
    response.risk_snapshot = [risk_view(risk) for risk in risks]
    response.status = ResponseStatus.SUBMITTED
    response.submitted_by = actor.id
    response.submitted_at = _now()
    bump(response, actor)
    item.status = ItemStatus.SUBMITTED
    bump(item, actor)
    await session.flush()
    await command.done(session, response.id, {"item_id": item.id, "revision": response.revision,
                                              "status": "submitted"})
    return response


# ── 复核 ────────────────────────────────────────────────────


async def review(
    session: AsyncSession, response_id: int, payload: ReviewIn, actor: User, client_key: str
) -> RcsaResponse:
    _, item, response = await _locked_response(session, response_id)
    command = Command(actor, "review", "RcsaResponse", response.id, client_key, payload.model_dump())
    if await command.replay(session) is not None:
        return response
    _require_reviewer(item, actor)
    # 管理员也没有例外：自己提交的，换成复核人之后也不能自己批。
    if actor.id in (response.submitted_by, item.assignee_id):
        raise Forbidden("复核人不能复核自己提交的评估", code="rcsa.reviewer_is_submitter")
    if response.status != ResponseStatus.SUBMITTED or item.current_response_id != response.id:
        raise Conflict("这一版已不是待复核的当前版本", code="rcsa.stale_revision",
                       params={"revision": response.revision})
    check_version(response, payload.expected_version)
    if payload.decision == "return" and not payload.reason:
        raise Conflict("退回需要写明理由", code="rcsa.reason_required")
    approve = payload.decision == "approve"
    response.status = ResponseStatus.APPROVED if approve else ResponseStatus.RETURNED
    bump(response, actor)
    item.status = ItemStatus.APPROVED if approve else ItemStatus.RETURNED
    bump(item, actor)
    session.add(RcsaReview(
        response_id=response.id,
        decision=ReviewDecision.APPROVE if approve else ReviewDecision.RETURN,
        target_version=response.revision, reviewer_id=actor.id, reason=payload.reason, at=_now(),
    ))
    await session.flush()
    await command.done(session, response.id, {"item_id": item.id, "revision": response.revision,
                                              "decision": payload.decision, "reason": payload.reason})
    return response


async def exclude(
    session: AsyncSession, item_id: int, reason: str, expected_version: int, actor: User, client_key: str
) -> RcsaItem:
    _, item = await locked_item(session, item_id)
    command = Command(actor, "exclude", "RcsaItem", item.id, client_key,
                      {"reason": reason, "expected_version": expected_version})
    if await command.replay(session) is not None:
        return item
    _require_reviewer(item, actor)
    if item.status in (ItemStatus.APPROVED, ItemStatus.EXCLUDED):
        raise Conflict("评估项当前状态不能排除", code="rcsa.item_state", params={"status": str(item.status)})
    check_version(item, expected_version)
    session.add(RcsaReview(item_id=item.id, decision=ReviewDecision.EXCLUDE, target_version=item.version,
                           reviewer_id=actor.id, reason=reason, at=_now()))
    item.status = ItemStatus.EXCLUDED
    bump(item, actor)
    await session.flush()
    await command.done(session, item.id, {"status": "excluded", "reason": reason})
    return item
