"""RCSA 2C：风险情景评分、五种处置、接受审批、整改行动与独立验证、发布到登记册。"""

from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.iam.permissions import Role
from app.rcsa.models import RcsaItemRisk
from app.risk.models import RiskEntry, RiskSource
from tests.rcsa_support import (
    COMPLETE_ANSWER,
    acme_world,
    activated,
    draft,
    person,
    rcsa_enabled,  # noqa: F401 — fixture
    review,
    save,
    submit,
    with_key,
)

pytestmark = pytest.mark.usefixtures("rcsa_enabled")

FUTURE = (date.today() + timedelta(days=180)).isoformat()
SOON = (date.today() + timedelta(days=90)).isoformat()

SCENARIO = {
    "scenario_key": "stale-access",
    "cause": "Leavers are not removed promptly",
    "event": "A former employee keeps access to the payroll system",
    "impact": "Unauthorised payroll changes",
    "inherent_likelihood": 4,
    "inherent_impact": 3,
    "inherent_rationale": "Manual leaver process",
    "residual_likelihood": 2,
    "residual_impact": 3,
    "residual_rationale": "Quarterly review catches most cases",
}


@pytest_asyncio.fixture
async def acme(client, db_session):
    world = await acme_world(client, db_session)
    world["approver"] = await person(client, db_session, "approver", Role.GRC_LEAD)
    campaign, items = await activated(client, world)
    return world | {"campaign": campaign, "item": items[0]}


async def _item(client, acme) -> dict:
    return (await client.get(f"/api/rcsa/items/{acme['item']['id']}", headers=acme["lead"].headers)).json()


async def _add_risk(client, acme, **overrides) -> dict:
    body = SCENARIO | {"owner_id": acme["assignee"].id} | overrides
    created = await client.post(f"/api/rcsa/items/{acme['item']['id']}/risks", json=body,
                                headers=acme["assignee"].headers)
    assert created.status_code == 201, created.text
    return created.json()


async def _approved_with_risk(client, acme) -> tuple[dict, dict]:
    """作答、加一条风险、提交、复核通过。返回（风险，已批准的响应）。"""
    response = await save(client, acme["assignee"], await draft(client, acme["assignee"], acme["item"]),
                          COMPLETE_ANSWER | {"no_risk_rationale": ""})
    risk = await _add_risk(client, acme)
    response = (await client.get(f"/api/rcsa/responses/{response['id']}", headers=acme["assignee"].headers)).json()
    submitted = await submit(client, acme["assignee"], response)
    assert submitted.status_code == 200, submitted.text
    approved = await review(client, acme["reviewer"], submitted.json(), "approve")
    assert approved.status_code == 200, approved.text
    return await _risk(client, acme, risk["id"]), approved.json()


async def _risk(client, acme, risk_id) -> dict:
    risks = (await client.get(f"/api/rcsa/items/{acme['item']['id']}/risks", headers=acme["lead"].headers)).json()
    return next(r for r in risks if r["id"] == risk_id)


async def _propose(client, who, risk, body):
    return await client.post(f"/api/rcsa/item-risks/{risk['id']}/disposition",
                             json=body | {"expected_version": risk["version"]}, headers=who.headers)


async def _decide(client, who, risk, decision="approve", reason="", key=None):
    return await client.post(
        f"/api/rcsa/item-risks/{risk['id']}/review-disposition",
        json={"decision": decision, "reason": reason, "expected_version": risk["version"]},
        headers=with_key(who, key or f"disp-{risk['id']}-{risk['version']}-{decision}"),
    )


async def _action(client, acme, risk, **overrides):
    body = {
        "title": "Automate leaver removal", "root_cause": "Manual HR handoff",
        "measure": "HR feed disables accounts on the leaving date", "owner_id": acme["assignee"].id,
        "verifier_id": acme["reviewer"].id, "due_at": SOON,
        "success_criteria": "No leaver account active after day one in a sample of 25",
    } | overrides
    return await client.post(f"/api/rcsa/item-risks/{risk['id']}/actions", json=body,
                             headers=acme["assignee"].headers)


ACCEPT = {
    "disposition": "accept", "accept_until": FUTURE, "reassess_at": SOON,
    "details": {"rationale": "Cost of automation exceeds exposure", "conditions": "Quarterly review continues"},
}


# ── 情景与评分 ──────────────────────────────────────────────


async def test_scores_are_computed_by_the_server(client, acme):
    risk = await _add_risk(client, acme)
    assert (risk["inherent_score"], risk["residual_score"]) == (12, 6)


async def test_score_outside_the_scale_is_rejected(client, acme):
    refused = await client.post(f"/api/rcsa/items/{acme['item']['id']}/risks",
                                json=SCENARIO | {"owner_id": acme["assignee"].id, "inherent_likelihood": 6},
                                headers=acme["assignee"].headers)
    assert refused.status_code == 422


async def test_residual_rating_needs_both_halves(client, acme):
    body = SCENARIO | {"owner_id": acme["assignee"].id, "residual_impact": None}
    refused = await client.post(f"/api/rcsa/items/{acme['item']['id']}/risks", json=body,
                                headers=acme["assignee"].headers)
    assert refused.status_code == 422


async def test_only_the_assignee_adds_scenarios(client, acme):
    refused = await client.post(f"/api/rcsa/items/{acme['item']['id']}/risks",
                                json=SCENARIO | {"owner_id": acme["assignee"].id}, headers=acme["reviewer"].headers)
    assert refused.status_code == 403
    assert refused.json()["code"] == "rcsa.not_assignee"


async def test_scenarios_are_locked_while_under_review(client, acme):
    response = await save(client, acme["assignee"], await draft(client, acme["assignee"], acme["item"]),
                          COMPLETE_ANSWER | {"no_risk_rationale": ""})
    risk = await _add_risk(client, acme)
    response = (await client.get(f"/api/rcsa/responses/{response['id']}", headers=acme["assignee"].headers)).json()
    assert (await submit(client, acme["assignee"], response)).status_code == 200
    refused = await client.patch(f"/api/rcsa/item-risks/{risk['id']}",
                                 json={"residual_likelihood": 1, "residual_impact": 1,
                                       "expected_version": risk["version"]},
                                 headers=acme["assignee"].headers)
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.item_under_review"


async def test_rescoring_an_approved_item_sends_it_back_for_assessment(client, acme):
    risk, _ = await _approved_with_risk(client, acme)

    rescored = await client.patch(f"/api/rcsa/item-risks/{risk['id']}",
                                  json={"residual_likelihood": 4, "residual_impact": 4,
                                        "expected_version": risk["version"]},
                                  headers=acme["assignee"].headers)

    assert rescored.status_code == 200, rescored.text
    item = await _item(client, acme)
    assert item["status"] == "draft"
    history = (await client.get(f"/api/rcsa/items/{item['id']}/responses", headers=acme["lead"].headers)).json()
    assert [(r["revision"], r["status"]) for r in history] == [(1, "approved"), (2, "draft")]
    assert history[0]["risk_snapshot"][0]["residual_score"] == 6


# ── 处置 ────────────────────────────────────────────────────


async def test_disposition_waits_for_an_approved_assessment(client, acme):
    risk = await _add_risk(client, acme)
    refused = await _propose(client, acme["assignee"], risk, ACCEPT)
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.item_not_approved"


async def test_acceptance_needs_rationale_conditions_and_dates(client, acme):
    risk, _ = await _approved_with_risk(client, acme)
    refused = await _propose(client, acme["assignee"], risk, {"disposition": "accept", "details": {}})
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.disposition_incomplete"
    assert {"rcsa.problem.accept_rationale", "rcsa.problem.accept_conditions", "rcsa.problem.accept_until",
            "rcsa.problem.reassess_at"} <= set(refused.json()["params"]["problems"])


async def test_acceptance_is_approved_by_someone_else_with_authority(client, acme):
    risk, _ = await _approved_with_risk(client, acme)
    proposed = await _propose(client, acme["assignee"], risk, ACCEPT)
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["disposition_status"] == "proposed"

    by_contributor = await _decide(client, acme["assignee"], proposed.json())
    assert by_contributor.status_code == 403

    approved = await _decide(client, acme["approver"], proposed.json())
    assert approved.status_code == 200, approved.text
    assert approved.json()["disposition_status"] == "approved"


async def test_approver_cannot_approve_their_own_proposal_even_as_admin(client, db_session, acme):
    admin = await person(client, db_session, "admin", Role.ADMIN)
    risk, _ = await _approved_with_risk(client, acme)
    owned = await client.patch(f"/api/rcsa/item-risks/{risk['id']}/owner",
                               json={"owner_id": admin.id, "expected_version": risk["version"]},
                               headers=acme["lead"].headers)
    assert owned.status_code == 200, owned.text
    proposed = (await _propose(client, admin, owned.json(), ACCEPT)).json()

    refused = await _decide(client, admin, proposed)

    assert refused.status_code == 403
    assert refused.json()["code"] == "rcsa.approver_is_proposer"


async def test_changing_strategy_keeps_the_earlier_approval_and_needs_a_new_one(client, acme):
    risk, _ = await _approved_with_risk(client, acme)
    first = await _decide(client, acme["approver"], (await _propose(client, acme["assignee"], risk, ACCEPT)).json())
    assert (await _action(client, acme, first.json())).status_code == 201
    current = await _risk(client, acme, risk["id"])

    second = await _propose(client, acme["assignee"], current, {"disposition": "reduce", "details": {}})

    assert second.status_code == 200, second.text
    assert (second.json()["disposition_status"], second.json()["disposition_revision"]) == ("proposed", 2)
    reviews = (await client.get(f"/api/rcsa/item-risks/{risk['id']}/reviews", headers=acme["lead"].headers)).json()
    assert [(r["decision"], r["target_version"]) for r in reviews] == [("approve_disposition", 1)]


async def test_reduce_needs_at_least_one_live_action(client, acme):
    risk, _ = await _approved_with_risk(client, acme)
    refused = await _propose(client, acme["assignee"], risk, {"disposition": "reduce", "details": {}})
    assert "rcsa.problem.reduce_action" in refused.json()["params"]["problems"]


async def test_transfer_that_is_not_yet_in_force_needs_a_milestone(client, acme):
    risk, _ = await _approved_with_risk(client, acme)
    refused = await _propose(client, acme["assignee"], risk, {
        "disposition": "transfer",
        "details": {"counterparty": "Acme insurer", "basis_ref": "Policy 12",
                    "retained_risk": "Deductible", "effective": False},
    })
    assert refused.json()["params"]["problems"] == ["rcsa.problem.milestone_action"]


async def test_return_of_a_disposition_needs_a_reason(client, acme):
    risk, _ = await _approved_with_risk(client, acme)
    proposed = (await _propose(client, acme["assignee"], risk, ACCEPT)).json()
    refused = await _decide(client, acme["approver"], proposed, decision="return")
    assert refused.json()["code"] == "rcsa.reason_required"


# ── 整改行动 ────────────────────────────────────────────────


async def test_verifier_must_differ_from_the_action_owner(client, acme):
    risk, _ = await _approved_with_risk(client, acme)
    refused = await _action(client, acme, risk, verifier_id=acme["assignee"].id)
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.verifier_is_owner"


async def test_action_is_done_only_when_an_independent_verifier_passes_it(client, acme):
    risk, _ = await _approved_with_risk(client, acme)
    action = (await _action(client, acme, risk)).json()

    claimed = await client.patch(f"/api/rcsa/actions/{action['id']}",
                                 json={"status": "pending_verification", "expected_version": action["version"]},
                                 headers=acme["assignee"].headers)
    assert claimed.json()["code"] == "rcsa.action_evidence_required"

    ready = await client.patch(f"/api/rcsa/actions/{action['id']}", json={
        "status": "pending_verification",
        "implementation_evidence": [{"description": "HR feed live", "location": "CHG-881"}],
        "expected_version": action["version"],
    }, headers=acme["assignee"].headers)
    assert ready.status_code == 200, ready.text

    self_check = await client.post(f"/api/rcsa/actions/{action['id']}/verify",
                                   json={"decision": "pass", "result": "Looks fine",
                                         "expected_version": ready.json()["version"]},
                                   headers=with_key(acme["assignee"], "v0"))
    assert self_check.status_code == 403

    failed = await client.post(f"/api/rcsa/actions/{action['id']}/verify",
                               json={"decision": "fail", "result": "3 of 25 leavers still active",
                                     "expected_version": ready.json()["version"]},
                               headers=with_key(acme["reviewer"], "v1"))
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "in_progress"

    again = await client.patch(f"/api/rcsa/actions/{action['id']}", json={
        "status": "pending_verification", "expected_version": failed.json()["version"],
    }, headers=acme["assignee"].headers)
    passed = await client.post(f"/api/rcsa/actions/{action['id']}/verify",
                               json={"decision": "pass", "result": "0 of 25 active",
                                     "expected_version": again.json()["version"]},
                               headers=with_key(acme["reviewer"], "v2"))
    assert passed.json()["status"] == "verified"
    assert passed.json()["verified_at"] is not None


async def test_cancelling_the_last_action_of_a_reduce_is_refused(client, acme):
    risk, _ = await _approved_with_risk(client, acme)
    action = (await _action(client, acme, risk)).json()
    proposed = (await _propose(client, acme["assignee"], await _risk(client, acme, risk["id"]),
                               {"disposition": "reduce", "details": {}})).json()
    assert (await _decide(client, acme["approver"], proposed)).status_code == 200

    refused = await client.post(f"/api/rcsa/actions/{action['id']}/cancel",
                                json={"reason": "No budget", "expected_version": action["version"]},
                                headers=acme["reviewer"].headers)

    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.disposition_needs_action"


# ── 登记册 ──────────────────────────────────────────────────


async def _publish(client, who, risk, key="pub-1", **body):
    return await client.post(f"/api/rcsa/item-risks/{risk['id']}/publish",
                             json={"mode": "create", "expected_version": risk["version"]} | body,
                             headers=with_key(who, key))


async def test_publishing_creates_one_register_risk_and_repeats_return_it(client, db_session, acme):
    risk, _ = await _approved_with_risk(client, acme)

    first = await _publish(client, acme["lead"], risk)
    again = await _publish(client, acme["lead"], risk, key="pub-2")

    assert first.status_code == 200, first.text
    assert again.json()["register_risk_id"] == first.json()["register_risk_id"]
    entries = list(await db_session.scalars(select(RiskEntry).where(RiskEntry.source == RiskSource.RCSA)))
    assert len(entries) == 1
    assert (entries[0].likelihood, entries[0].impact, entries[0].residual_score) == (4, 3, 6)
    assert entries[0].source_ref["rcsa_item_risk_id"] == risk["id"]


async def test_register_edits_do_not_flow_back_into_the_assessment(client, db_session, acme):
    risk, _ = await _approved_with_risk(client, acme)
    published = (await _publish(client, acme["lead"], risk)).json()
    entry = await db_session.get(RiskEntry, published["register_risk_id"])
    entry.likelihood = 1
    await db_session.flush()
    assert (await db_session.get(RcsaItemRisk, risk["id"])).inherent_likelihood == 4


async def test_only_managers_publish(client, acme):
    risk, _ = await _approved_with_risk(client, acme)
    refused = await _publish(client, acme["assignee"], risk)
    assert refused.status_code == 403


async def test_unapproved_assessments_are_not_published(client, db_session, acme):
    risk = await _add_risk(client, acme)
    refused = await _publish(client, acme["lead"], risk)
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.item_not_approved"
    assert await db_session.scalar(select(func.count()).select_from(RiskEntry)) == 0


async def test_an_acceptance_near_expiry_reminds_its_owner_and_its_approver(client, db_session, acme):
    risk, _ = await _approved_with_risk(client, acme)
    soon = (date.today() + timedelta(days=10)).isoformat()
    proposed = (await _propose(client, acme["assignee"], risk, ACCEPT | {
        "accept_until": soon, "reassess_at": soon})).json()
    assert (await _decide(client, acme["approver"], proposed)).status_code == 200

    def acceptances(work):
        return [(row["kind"], row["id"], row["status"]) for row in work["rows"] if row["kind"] == "acceptance"]

    for who in ("assignee", "approver"):
        work = (await client.get("/api/rcsa/my-work", headers=acme[who].headers)).json()
        assert acceptances(work) == [("acceptance", risk["id"], "expiring")], who
    reviewer_work = (await client.get("/api/rcsa/my-work", headers=acme["reviewer"].headers)).json()
    assert acceptances(reviewer_work) == []
