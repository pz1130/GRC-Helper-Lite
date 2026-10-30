"""RCSA 恢复核对：在合成库上走完一期活动，算一份「恢复后必须一模一样」的指纹。

    python -m tests.rcsa_drill build        # 造数据（e2e 种子 + 一期走到关闭）
    python -m tests.rcsa_drill fingerprint  # 打印指纹 JSON

由 scripts/rcsa-restore-check.sh 在源库和恢复出来的库上各跑一次 fingerprint 再比对。
用的是服务层而不是直接插表：要核对的正是正常业务写进去的东西（含审计与幂等键）。
"""

import asyncio
import hashlib
import json
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.controls.models import Control
from app.frameworks.models import Framework
from app.iam.models import AuditLog, User
from app.rcsa import campaigns, closing, kri, report, responses, risks, triggers
from app.rcsa.models import (
    RcsaCampaign,
    RcsaItem,
    RcsaItemRisk,
    RcsaResponse,
    RcsaScoringProfile,
    RcsaTrigger,
)
from app.rcsa.schemas import (
    ActionIn,
    AnswerV1,
    CampaignCreateIn,
    CloseIn,
    DispositionIn,
    DispositionReviewIn,
    EvidenceIn,
    ItemsAddIn,
    KriIn,
    KriObservationIn,
    KriStateIn,
    PublishIn,
    ReviewIn,
    RiskIn,
    TriggerDecideIn,
    TriggerIn,
    TriggerLinkIn,
)
from tests.e2e_rcsa_seed import seed

RCSA_TABLES = (
    "rcsa_scoring_profiles", "rcsa_campaigns", "rcsa_items", "rcsa_responses", "rcsa_response_evidence",
    "rcsa_item_risks", "rcsa_actions", "rcsa_triggers", "rcsa_trigger_controls", "rcsa_reviews",
    "rcsa_kris", "rcsa_kri_observations",
)
ANSWER = {
    "design": {"rating": "effective", "rationale": "Every privileged account is on the list"},
    "operating": {"rating": "partially_effective", "rationale": "Two reviews closed late"},
    "assessment_period_start": "2026-01-01", "assessment_period_end": "2026-03-31",
    "independent_test_status": "not_provided", "independent_test_reason": "No audit coverage this quarter",
}


async def _user(session: AsyncSession, email: str) -> User:
    return await session.scalar(select(User).where(User.email == email))


async def build(session: AsyncSession) -> int:
    await seed(session)
    lead = await _user(session, "rcsa-lead@example.com")
    assessor = await _user(session, "rcsa-assessor@example.com")
    reviewer = await _user(session, "rcsa-reviewer@example.com")
    framework = await session.scalar(select(Framework).where(Framework.key == "acme-rcsa"))
    profile = await session.scalar(select(RcsaScoringProfile).where(RcsaScoringProfile.status == "approved"))
    controls = list(await session.scalars(select(Control).order_by(Control.code)))
    soon = date.today() + timedelta(days=90)

    campaign = await campaigns.create_campaign(session, CampaignCreateIn(
        name="Acme drill", framework_id=framework.id, profile_id=profile.id, period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31), due_at=soon,
    ), lead)
    first, second = await campaigns.add_items(session, campaign.id, ItemsAddIn(
        control_ids=[c.id for c in controls], reviewer_id=reviewer.id), lead)
    await campaigns.activate(session, campaign.id, campaign.version, lead, "drill-activate")

    await session.refresh(first)
    response = await responses.open_draft(session, first.id, first.version, assessor)
    response = await responses.save_answer(session, response.id, AnswerV1.model_validate(ANSWER),
                                           response.version, assessor)
    await responses.attach_evidence(session, response.id, EvidenceIn(
        external_location="Ticket ACME-77", purpose="self_assessment"), assessor)
    risk = await risks.add_risk(session, first.id, RiskIn(
        scenario_key="stale-access", cause="Manual leaver process", event="Leaver keeps access",
        impact="Unauthorised change", inherent_likelihood=4, inherent_impact=4, inherent_rationale="Manual",
        residual_likelihood=2, residual_impact=3, residual_rationale="Quarterly review", owner_id=assessor.id,
    ), assessor)
    await session.refresh(response)
    response = await responses.submit(session, response.id, response.version, assessor, "drill-submit")
    await responses.review(session, response.id, ReviewIn(decision="approve", expected_version=response.version),
                           reviewer, "drill-review")
    await risks.add_action(session, risk.id, ActionIn(
        title="Automate leavers", owner_id=assessor.id, verifier_id=reviewer.id, due_at=soon,
        success_criteria="No leaver active after day one",
    ), assessor)
    await session.refresh(risk)
    risk = await risks.propose(session, risk.id, DispositionIn(
        disposition="accept", accept_until=soon, reassess_at=soon - timedelta(days=30),
        details={"rationale": "Exposure is low", "conditions": "Quarterly review continues"},
        expected_version=risk.version,
    ), assessor)
    risk = await risks.review_disposition(session, risk.id, DispositionReviewIn(
        decision="approve", expected_version=risk.version), lead, "drill-accept")
    await risks.publish(session, risk.id, PublishIn(mode="create", expected_version=risk.version), lead, "drill-publish")

    await session.refresh(second)
    await responses.exclude(session, second.id, "Platform decommissioned", second.version, reviewer, "drill-exclude")

    trigger = await triggers.register(session, TriggerIn(
        trigger_type="incident", title="Access review missed in February", source_ref="INC-0042",
        occurred_at=datetime(2026, 2, 20, tzinfo=UTC), owner_id=lead.id, decision_due_at=soon,
        control_ids=[controls[0].id],
    ), assessor)
    trigger = await triggers.triage(session, trigger.id, trigger.version, lead)
    trigger = await triggers.decide(session, trigger.id, TriggerDecideIn(
        decision="reassess", rationale="Key control", expected_version=trigger.version), lead)
    await triggers.link(session, trigger.id, TriggerLinkIn(campaign_id=campaign.id, expected_version=trigger.version), lead)

    await session.refresh(campaign)
    campaign = await closing.begin_review(session, campaign.id, campaign.version, lead)
    await closing.close(session, campaign.id, CloseIn(expected_version=campaign.version), lead, "drill-close")
    indicator = await kri.create(session, KriIn(name="Acme review delay", source="Acme drill", unit="count",
        control_id=controls[0].id, direction="high", amber_threshold=5, red_threshold=10, owner_id=lead.id), lead)
    await kri.configure(session, indicator.id, KriStateIn(enabled=True, owner_id=lead.id,
                                                        expected_version=indicator.version), lead)
    await kri.ingest(session, indicator.id, KriObservationIn(external_id="drill-1", value=0,
        source_ref="ACME-DRILL-1", observed_at=datetime(2026, 2, 20, tzinfo=UTC)), lead)
    await session.flush()
    return campaign.id


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


async def fingerprint(session: AsyncSession) -> dict[str, Any]:
    rows = {
        table: int(await session.scalar(text(f"SELECT count(*) FROM {table}")))
        for table in RCSA_TABLES
    }
    profiles = [
        [p.key, p.revision, str(p.status), p.horizon, _digest(p.definitions), _digest(p.bands)]
        for p in await session.scalars(select(RcsaScoringProfile).order_by(RcsaScoringProfile.id))
    ]
    response_rows = [
        [r.item_id, r.revision, str(r.status), r.version, _digest(r.answer), _digest(r.risk_snapshot)]
        for r in await session.scalars(select(RcsaResponse).order_by(RcsaResponse.id))
    ]
    audit_rows = [
        [a.action, a.entity_type, a.entity_id, (a.after or {}).get("command_key")]
        for a in await session.scalars(
            select(AuditLog).where(AuditLog.action.like("rcsa.%")).order_by(AuditLog.id)
        )
    ]
    trigger_rows = [
        [t.id, str(t.status), str(t.disposition), t.campaign_id, t.version]
        for t in await session.scalars(select(RcsaTrigger).order_by(RcsaTrigger.id))
    ]
    closed_reports = {}
    for campaign in await session.scalars(select(RcsaCampaign).where(RcsaCampaign.closed_snapshot.is_not(None))):
        built = await report.build(session, campaign.id)
        built.pop("generated_at")
        closed_reports[str(campaign.id)] = [_digest(campaign.closed_snapshot), _digest(built)]
    db_triggers = sorted(await session.scalars(text(
        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'rcsa_%'"
    )))
    return {
        "rows": rows,
        "kris": {table: _digest(await session.scalar(text(
            f"SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM {table} t")))
            for table in ("rcsa_kris", "rcsa_kri_observations")},
        "profiles": profiles,
        "responses": response_rows,
        "audit": audit_rows,
        "triggers": trigger_rows,
        "register_risks": int(await session.scalar(text("SELECT count(*) FROM risk_entries WHERE source = 'rcsa'"))),
        "register_links": sorted(await session.scalars(
            select(RcsaItemRisk.register_risk_id).where(RcsaItemRisk.register_risk_id.is_not(None))
        )),
        "current_pointers": sorted(
            [i.id, i.current_response_id] for i in await session.scalars(select(RcsaItem).order_by(RcsaItem.id))
        ),
        "closed_reports": closed_reports,
        "db_triggers": db_triggers,
        "max_ids": {"campaigns": await session.scalar(select(func.max(RcsaCampaign.id)))},
    }


async def _main(command: str) -> None:
    import app.models  # noqa: F401 — 注册全部模型
    from app.db import session_factory

    async with session_factory() as session:
        if command == "build":
            print(f"closed campaign {await build(session)}")
            await session.commit()
        else:
            print(json.dumps(await fingerprint(session), sort_keys=True, indent=1))


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else "fingerprint"))
