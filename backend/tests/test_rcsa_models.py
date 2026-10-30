"""RCSA 表的数据库级约束。

这些不变量由数据库兜底，而不是只靠服务层：并发、脚本、以后新写的接口都绕不过去。
每条测试都是一种「服务层写错了也不能落库」的非法数据。
"""

from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from app.controls.models import Control
from app.evidence.models import EvidenceCadence, EvidenceItem, EvidenceStatus, EvidenceType
from app.frameworks.models import Framework
from app.iam.models import AuditLog, User
from app.iam.permissions import Role
from app.rcsa.models import (
    RcsaAction,
    RcsaCampaign,
    RcsaItem,
    RcsaItemRisk,
    RcsaResponse,
    RcsaResponseEvidence,
    RcsaReview,
    RcsaScoringProfile,
    RcsaTrigger,
    RcsaTriggerControl,
)
from app.risk.models import RiskEntry, RiskSource, RiskStatus

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


async def _user(db_session, email: str, role: Role) -> User:
    user = User(email=email, name=email.split("@")[0], role=role, password_hash="x")
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def world(db_session):
    """一个草稿活动：两个控制点、一个评估项，作答人与复核人不同。"""
    owner = await _user(db_session, "owner@acme.test", Role.GRC_LEAD)
    assignee = await _user(db_session, "assignee@acme.test", Role.CONTRIBUTOR)
    reviewer = await _user(db_session, "reviewer@acme.test", Role.GRC_LEAD)
    framework = Framework(key="acme-it", name_zh="Acme IT", name_en="Acme IT", version="1")
    control = Control(code="C-9001", title="Quarterly access review", statement="Access is reviewed.")
    other = Control(code="C-9002", title="Backup testing", statement="Backups are tested.")
    db_session.add_all([framework, control, other])
    await db_session.flush()
    profile = RcsaScoringProfile(
        key="acme-5x5", revision=1, algorithm="product", definitions={}, bands=[],
        horizon="", status="draft", created_by=owner.id, updated_by=owner.id,
    )
    db_session.add(profile)
    await db_session.flush()
    campaign = RcsaCampaign(
        name="Acme Q1", framework_id=framework.id, profile_id=profile.id,
        period_start=date(2026, 1, 1), period_end=date(2026, 3, 31), due_at=date(2026, 4, 15),
        timezone="UTC", owner_id=owner.id, status="draft",
        created_by=owner.id, updated_by=owner.id,
    )
    db_session.add(campaign)
    await db_session.flush()
    item = RcsaItem(
        campaign_id=campaign.id, control_id=control.id, assignee_id=assignee.id,
        reviewer_id=reviewer.id, status="pending", created_by=owner.id, updated_by=owner.id,
    )
    db_session.add(item)
    await db_session.flush()
    return {
        "owner": owner, "assignee": assignee, "reviewer": reviewer, "framework": framework,
        "control": control, "other": other, "profile": profile, "campaign": campaign, "item": item,
    }


async def _rejected(db_session, *rows) -> None:
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add_all(rows)
            await db_session.flush()


async def _rejected_sql(db_session, statement) -> None:
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(statement)


def _response(item: RcsaItem, revision: int, status: str = "draft", **extra) -> RcsaResponse:
    fields = {
        "item_id": item.id, "revision": revision, "status": status,
        "answer": {"design": {"rating": "effective"}}, "created_by": item.created_by,
        "updated_by": item.created_by,
    }
    if status != "draft":
        fields |= {"submitted_by": item.created_by, "submitted_at": NOW, "risk_snapshot": []}
    return RcsaResponse(**(fields | extra))


def _risk(item: RcsaItem, key: str = "s1", **extra) -> RcsaItemRisk:
    fields = {
        "item_id": item.id, "scenario_key": key, "cause": "c", "event": "e", "impact": "i",
        "inherent_likelihood": 4, "inherent_impact": 3, "inherent_score": 12,
        "owner_id": item.assignee_id, "created_by": item.assignee_id,
        "updated_by": item.assignee_id,
    }
    return RcsaItemRisk(**(fields | extra))


# ── 评分与情景 ──────────────────────────────────────────────


async def test_likelihood_outside_one_to_five_is_rejected(db_session, world):
    await _rejected(db_session, _risk(world["item"], inherent_likelihood=6, inherent_score=18))


async def test_residual_rating_needs_both_likelihood_and_impact(db_session, world):
    await _rejected(db_session, _risk(world["item"], residual_likelihood=2))


async def test_same_scenario_key_twice_on_one_item_is_rejected(db_session, world):
    db_session.add(_risk(world["item"]))
    await db_session.flush()
    await _rejected(db_session, _risk(world["item"]))


async def test_proposed_acceptance_without_expiry_is_rejected(db_session, world):
    await _rejected(
        db_session,
        _risk(
            world["item"], disposition="accept", disposition_status="proposed",
            disposition_proposed_by=world["assignee"].id, disposition_proposed_at=NOW,
            reassess_at=date(2026, 6, 30),
        ),
    )


async def test_approved_profile_needs_approver_and_horizon(db_session, world):
    profile = world["profile"]
    await _rejected_sql(
        db_session,
        update(RcsaScoringProfile).where(RcsaScoringProfile.id == profile.id).values(status="approved"),
    )


# ── 活动与评估项 ────────────────────────────────────────────


async def test_campaign_period_must_not_end_before_it_starts(db_session, world):
    await _rejected_sql(
        db_session,
        update(RcsaCampaign)
        .where(RcsaCampaign.id == world["campaign"].id)
        .values(period_end=date(2025, 12, 31)),
    )


async def test_same_control_twice_in_one_campaign_is_rejected(db_session, world):
    item = world["item"]
    await _rejected(
        db_session,
        RcsaItem(
            campaign_id=item.campaign_id, control_id=item.control_id, status="pending",
            created_by=item.created_by, updated_by=item.created_by,
        ),
    )


async def test_assignee_cannot_also_be_reviewer(db_session, world):
    await _rejected(
        db_session,
        RcsaItem(
            campaign_id=world["campaign"].id, control_id=world["other"].id,
            assignee_id=world["assignee"].id, reviewer_id=world["assignee"].id, status="pending",
            created_by=world["owner"].id, updated_by=world["owner"].id,
        ),
    )


async def test_closed_campaign_row_cannot_be_changed(db_session, world):
    campaign = world["campaign"]
    await db_session.execute(
        update(RcsaCampaign).where(RcsaCampaign.id == campaign.id).values(
            status="closed", framework_snapshot={}, profile_snapshot={}, closed_snapshot={},
            closed_by=world["owner"].id, closed_at=NOW,
        )
    )
    await _rejected_sql(
        db_session,
        update(RcsaCampaign).where(RcsaCampaign.id == campaign.id).values(name="Rewritten"),
    )


async def test_cancelled_campaign_needs_a_reason(db_session, world):
    await _rejected_sql(
        db_session,
        update(RcsaCampaign).where(RcsaCampaign.id == world["campaign"].id).values(status="cancelled"),
    )


# ── 响应版本 ────────────────────────────────────────────────


async def test_same_revision_twice_is_rejected(db_session, world):
    db_session.add(_response(world["item"], 1, "submitted"))
    await db_session.flush()
    await _rejected(db_session, _response(world["item"], 1, "draft"))


async def test_only_one_draft_per_item(db_session, world):
    db_session.add(_response(world["item"], 1))
    await db_session.flush()
    await _rejected(db_session, _response(world["item"], 2))


async def test_current_response_must_belong_to_the_item(db_session, world):
    other_item = RcsaItem(
        campaign_id=world["campaign"].id, control_id=world["other"].id, status="pending",
        created_by=world["owner"].id, updated_by=world["owner"].id,
    )
    db_session.add(other_item)
    await db_session.flush()
    foreign = _response(other_item, 1)
    db_session.add(foreign)
    await db_session.flush()
    await _rejected_sql(
        db_session,
        update(RcsaItem).where(RcsaItem.id == world["item"].id).values(current_response_id=foreign.id),
    )


async def test_submitted_answer_is_frozen_but_its_review_status_can_move(db_session, world):
    response = _response(world["item"], 1, "submitted")
    db_session.add(response)
    await db_session.flush()
    await _rejected_sql(
        db_session,
        update(RcsaResponse).where(RcsaResponse.id == response.id).values(answer={"design": {}}),
    )
    await db_session.execute(
        update(RcsaResponse).where(RcsaResponse.id == response.id).values(status="approved")
    )
    assert await db_session.scalar(
        select(RcsaResponse.status).where(RcsaResponse.id == response.id)
    ) == "approved"


async def test_evidence_cannot_be_attached_after_submission(db_session, world):
    response = _response(world["item"], 1, "submitted")
    db_session.add(response)
    await db_session.flush()
    await _rejected(
        db_session,
        RcsaResponseEvidence(
            response_id=response.id, purpose="self_assessment",
            external_location="Ticket ACME-1", snapshot={}, created_by=world["assignee"].id,
        ),
    )


async def test_evidence_needs_a_registered_item_or_an_external_location(db_session, world):
    response = _response(world["item"], 1)
    db_session.add(response)
    await db_session.flush()
    await _rejected(
        db_session,
        RcsaResponseEvidence(
            response_id=response.id, purpose="self_assessment", snapshot={},
            created_by=world["assignee"].id,
        ),
    )


async def test_independent_test_label_needs_tester_date_scope_and_basis(db_session, world):
    response = _response(world["item"], 1)
    db_session.add(response)
    await db_session.flush()
    await _rejected(
        db_session,
        RcsaResponseEvidence(
            response_id=response.id, purpose="independent_test", external_location="Report 7",
            snapshot={}, tester_name="Internal audit", conclusion="No exceptions",
            created_by=world["assignee"].id,
        ),
    )


async def test_referenced_evidence_item_cannot_be_deleted_underneath(db_session, world):
    evidence_type = EvidenceType(
        name_zh="Review log", name_en="Review log", cadence=EvidenceCadence.QUARTERLY
    )
    db_session.add(evidence_type)
    await db_session.flush()
    evidence = EvidenceItem(
        evidence_type_id=evidence_type.id, control_id=world["control"].id,
        title="Q1 review export", status=EvidenceStatus.PLANNED,
    )
    db_session.add(evidence)
    await db_session.flush()
    response = _response(world["item"], 1)
    db_session.add(response)
    await db_session.flush()
    db_session.add(
        RcsaResponseEvidence(
            response_id=response.id, evidence_item_id=evidence.id, purpose="self_assessment",
            snapshot={"title": evidence.title}, created_by=world["assignee"].id,
        )
    )
    await db_session.flush()
    await _rejected_sql(db_session, text("DELETE FROM evidence_items WHERE id = :id").bindparams(id=evidence.id))


# ── 复核记录 ────────────────────────────────────────────────


async def test_review_must_target_exactly_one_record(db_session, world):
    response = _response(world["item"], 1, "submitted")
    db_session.add(response)
    await db_session.flush()
    reviewer = world["reviewer"].id
    await _rejected(
        db_session,
        RcsaReview(decision="approve", target_version=1, reviewer_id=reviewer, at=NOW),
    )
    await _rejected(
        db_session,
        RcsaReview(
            response_id=response.id, item_id=world["item"].id, decision="approve",
            target_version=1, reviewer_id=reviewer, at=NOW,
        ),
    )


async def test_review_decision_must_fit_its_target(db_session, world):
    await _rejected(
        db_session,
        RcsaReview(
            item_id=world["item"].id, decision="approve", target_version=1,
            reviewer_id=world["reviewer"].id, at=NOW,
        ),
    )


async def test_returning_a_response_needs_a_reason(db_session, world):
    response = _response(world["item"], 1, "submitted")
    db_session.add(response)
    await db_session.flush()
    await _rejected(
        db_session,
        RcsaReview(
            response_id=response.id, decision="return", target_version=1,
            reviewer_id=world["reviewer"].id, reason="  ", at=NOW,
        ),
    )


async def test_reviews_are_append_only(db_session, world):
    review = RcsaReview(
        item_id=world["item"].id, decision="exclude", target_version=1,
        reviewer_id=world["reviewer"].id, reason="Decommissioned in Q4", at=NOW,
    )
    db_session.add(review)
    await db_session.flush()
    await _rejected_sql(
        db_session, update(RcsaReview).where(RcsaReview.id == review.id).values(reason="edited")
    )
    await _rejected_sql(db_session, text("DELETE FROM rcsa_reviews WHERE id = :id").bindparams(id=review.id))


# ── 行动与触发 ──────────────────────────────────────────────


async def test_action_owner_cannot_verify_their_own_action(db_session, world):
    risk = _risk(world["item"])
    db_session.add(risk)
    await db_session.flush()
    await _rejected(
        db_session,
        RcsaAction(
            item_risk_id=risk.id, title="Automate review", owner_id=world["assignee"].id,
            verifier_id=world["assignee"].id, due_at=date(2026, 5, 1),
            success_criteria="Export shows zero stale accounts", status="open",
            created_by=world["owner"].id, updated_by=world["owner"].id,
        ),
    )


async def test_planned_change_trigger_needs_planned_effective_date(db_session, world):
    await _rejected(
        db_session,
        RcsaTrigger(
            trigger_type="planned_change", title="IdP migration", source_ref="CHG-12",
            occurred_at=NOW, owner_id=world["owner"].id, decision_due_at=date(2026, 2, 1),
            status="registered", created_by=world["owner"].id, updated_by=world["owner"].id,
        ),
    )


async def test_excluding_a_trigger_control_needs_a_reason(db_session, world):
    trigger = RcsaTrigger(
        trigger_type="incident", title="Backup job failed", source_ref="INC-3",
        occurred_at=NOW, owner_id=world["owner"].id, decision_due_at=date(2026, 2, 1),
        status="registered", created_by=world["owner"].id, updated_by=world["owner"].id,
    )
    db_session.add(trigger)
    await db_session.flush()
    await _rejected(
        db_session,
        RcsaTriggerControl(
            trigger_id=trigger.id, control_id=world["other"].id, control_snapshot={},
            coverage_disposition="excluded",
        ),
    )


# ── 登记册与幂等 ────────────────────────────────────────────


def _register_risk(item_risk_id: int) -> RiskEntry:
    return RiskEntry(
        title="Stale access", source=RiskSource.RCSA, source_ref={"rcsa_item_risk_id": item_risk_id},
        likelihood=4, impact=3, inherent_score=12, status=RiskStatus.OPEN,
    )


async def test_one_rcsa_scenario_publishes_at_most_one_register_risk(db_session, world):
    risk = _risk(world["item"])
    db_session.add(risk)
    await db_session.flush()
    db_session.add(_register_risk(risk.id))
    await db_session.flush()
    await _rejected(db_session, _register_risk(risk.id))


async def test_rcsa_command_key_is_unique_but_other_audit_rows_are_untouched(db_session, world):
    def row(action: str) -> AuditLog:
        return AuditLog(
            user_id=world["owner"].id, action=action, entity_type="RcsaCampaign", entity_id="1",
            after={"command_key": "u1:activate:campaign-1:k1", "request_hash": "h"},
        )

    db_session.add(row("rcsa.activate"))
    await db_session.flush()
    await _rejected(db_session, row("rcsa.activate"))
    # 旧日志不在索引范围内：同样的键出现在非 RCSA 动作里不受影响。
    db_session.add_all([row("control.update"), row("control.update")])
    await db_session.flush()
