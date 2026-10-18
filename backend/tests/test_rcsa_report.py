"""RCSA 报告：闭期以快照为准、未闭期带 Draft、取消带 Cancelled；当前跟进另列。以及可指派人员。"""

import json

import pytest
import pytest_asyncio

from app.audit.models import AuditEngagement
from app.iam.permissions import Role
from tests.rcsa_support import (
    acme_world,
    activated,
    approve_disposition,
    finish_items,
    get_json,
    person,
    rcsa_enabled,  # noqa: F401 — fixture
    transition,
    with_key,
)

pytestmark = pytest.mark.usefixtures("rcsa_enabled")


@pytest_asyncio.fixture
async def acme(client, db_session):
    world = await acme_world(client, db_session)
    world["approver"] = await person(client, db_session, "approver", Role.GRC_LEAD)
    campaign, items = await activated(client, world, controls=3)
    return world | {"campaign": campaign, "items": items}


async def _report(client, acme) -> dict:
    return await get_json(client, acme, f"/api/rcsa/campaigns/{acme['campaign']['id']}/report")


async def test_people_lists_who_can_answer_and_review(client, db_session, acme):
    engagement = AuditEngagement(name="Acme audit", audit_type="external", status="preparing")
    db_session.add(engagement)
    await db_session.flush()
    gone = await person(client, db_session, "gone", Role.CONTRIBUTOR)
    gone.user.is_active = False
    outsider = await person(client, db_session, "outsider", Role.VIEWER)
    outsider.user.engagement_scope_id = engagement.id
    await db_session.flush()

    people = {p["name"]: p for p in await get_json(client, acme, "/api/rcsa/people")}

    assert "Gone" not in people and "Outsider" not in people
    assert (people["Assignee"]["can_respond"], people["Assignee"]["can_review"]) == (True, False)
    assert (people["Reviewer"]["can_review"], people["Reviewer"]["can_accept_risk"]) == (True, True)
    assert people["Viewer"]["can_respond"] is False


async def test_open_campaign_report_is_marked_draft_and_lists_gaps(client, acme):
    found = await finish_items(client, acme)
    assert found

    report = await _report(client, acme)

    assert (report["watermark"], report["source"]) == ("draft", "live")
    assert report["scope"] == {"total": 3, "approved": 2, "excluded": 1, "submitted": 0, "in_progress": 0}
    assert report["exclusions"] == [{"control_code": "C-9003", "reason": "Decommissioned in January"}]
    assert [(row["control_code"], row["ratings"]) for row in report["not_assessed"]] == [("C-9002", ["operating"])]
    assert report["independent_tests"] == {"provided": 0, "not_provided": 2, "not_applicable": 0}
    assert report["scenario_bands"]["inherent"] == {"High": 1}
    assert report["scenario_bands"]["residual"] == {"Moderate": 1}
    assert report["dispositions"] == {"reduce": 1}


async def test_closed_report_comes_from_the_snapshot_and_follow_up_is_listed_apart(client, acme):
    found = await finish_items(client, acme)
    await transition(client, acme, "begin-review")
    await approve_disposition(client, acme, found["risk"])
    assert (await transition(client, acme, "close")).status_code == 200
    action = found["action"]
    ready = await client.patch(f"/api/rcsa/actions/{action['id']}", json={
        "status": "pending_verification", "implementation_evidence": [{"description": "Feed live"}],
        "expected_version": action["version"]}, headers=acme["assignee"].headers)
    await client.post(f"/api/rcsa/actions/{action['id']}/verify",
                      json={"decision": "pass", "result": "0 of 25", "expected_version": ready.json()["version"]},
                      headers=with_key(acme["reviewer"], "v"))

    report = await _report(client, acme)

    assert (report["watermark"], report["source"]) == (None, "closed_snapshot")
    assert report["actions"] == {"open": 1, "overdue": 0, "verified": 0, "cancelled": 0}
    assert [(a["id"], a["status"]) for a in report["current_follow_up"]["actions"]] == [(action["id"], "verified")]


async def test_cancelled_campaign_report_is_marked_cancelled(client, acme):
    await transition(client, acme, "cancel", {"reason": "Superseded"})
    assert (await _report(client, acme))["watermark"] == "cancelled"


async def test_report_carries_no_evidence_locations(client, db_session, acme):
    item = acme["items"][1]
    from tests.rcsa_support import COMPLETE_ANSWER, draft, review, save, submit

    response = await save(client, acme["assignee"], await draft(client, acme["assignee"], item), COMPLETE_ANSWER)
    await client.post(f"/api/rcsa/responses/{response['id']}/evidence", json={
        "external_location": "\\\\fileserver01\\acme\\rcsa\\q1-export.xlsx", "purpose": "self_assessment",
    }, headers=acme["assignee"].headers)
    response = await get_json(client, acme, f"/api/rcsa/responses/{response['id']}")
    submitted = (await submit(client, acme["assignee"], response)).json()
    assert (await review(client, acme["reviewer"], submitted, "approve")).status_code == 200

    report = json.dumps(await _report(client, acme))

    assert "fileserver01" not in report


# ── 跨期比较 ────────────────────────────────────────────────


async def _successor(client, acme, *, profile_id: int | None = None) -> dict:
    """接续上一期、只含第一个控制点的新活动，已激活。"""
    from tests.rcsa_support import CAMPAIGN_BODY

    body = CAMPAIGN_BODY | {
        "name": "Acme Q2", "framework_id": acme["lib"].framework.id,
        "profile_id": profile_id or acme["profile"].id, "predecessor_campaign_id": acme["campaign"]["id"],
    }
    campaign = (await client.post("/api/rcsa/campaigns", json=body, headers=acme["lead"].headers)).json()
    await client.post(f"/api/rcsa/campaigns/{campaign['id']}/items",
                      json={"control_ids": [acme["lib"].controls[0].id], "reviewer_id": acme["reviewer"].id},
                      headers=acme["lead"].headers)
    active = await client.post(f"/api/rcsa/campaigns/{campaign['id']}/activate",
                               json={"expected_version": campaign["version"]}, headers=with_key(acme["lead"], "q2"))
    assert active.status_code == 200, active.text
    return active.json()


async def _rate(client, acme, campaign, residual: tuple[int, int], key: str = "stale-access") -> None:
    item = (await client.get(f"/api/rcsa/campaigns/{campaign['id']}/items", headers=acme["lead"].headers)).json()[0]
    from tests.rcsa_support import draft

    await draft(client, acme["assignee"], item)
    created = await client.post(f"/api/rcsa/items/{item['id']}/risks", json={
        "scenario_key": key, "cause": "Manual leaver process", "event": "Leaver keeps access",
        "impact": "Unauthorised change", "inherent_likelihood": 4, "inherent_impact": 4, "inherent_rationale": "r",
        "residual_likelihood": residual[0], "residual_impact": residual[1], "residual_rationale": "r",
        "owner_id": acme["assignee"].id,
    }, headers=acme["assignee"].headers)
    assert created.status_code == 201, created.text


async def _closed_first(client, acme) -> None:
    found = await finish_items(client, acme)
    await transition(client, acme, "begin-review")
    await approve_disposition(client, acme, found["risk"])
    assert (await transition(client, acme, "close")).status_code == 200


async def test_same_profile_scenarios_are_shown_side_by_side_without_percentages(client, acme):
    await _closed_first(client, acme)
    successor = await _successor(client, acme)
    await _rate(client, acme, successor, residual=(2, 2))

    report = await get_json(client, acme, f"/api/rcsa/campaigns/{successor['id']}/report")

    comparison = report["comparison"]
    assert (comparison["comparable"], comparison["reason"]) == (True, None)
    assert comparison["scenarios"] == [{
        "control_code": "C-9001", "scenario_key": "stale-access", "status": "comparable",
        "previous_residual_score": 9, "previous_residual_band": "Moderate",
        "current_residual_score": 4, "current_residual_band": "Low",
    }]
    assert "%" not in json.dumps(report) and "improvement" not in json.dumps(report)
    assert report["highest_residual_band"] == "Low"
    assert report["ai_assistance"] == "not_enabled"


async def test_a_new_scenario_key_is_not_compared_with_an_old_one(client, acme):
    await _closed_first(client, acme)
    successor = await _successor(client, acme)
    await _rate(client, acme, successor, residual=(2, 2), key="shared-admin")

    statuses = {(s["scenario_key"], s["status"]) for s in
                (await get_json(client, acme, f"/api/rcsa/campaigns/{successor['id']}/report"))["comparison"]["scenarios"]}

    assert statuses == {("shared-admin", "new"), ("stale-access", "removed")}


async def test_another_scoring_revision_is_not_directly_comparable(client, db_session, acme):
    from app.rcsa.models import RcsaScoringProfile
    from tests.rcsa_support import CALIBRATED_DEFINITIONS

    await _closed_first(client, acme)
    revised = RcsaScoringProfile(
        key=acme["profile"].key, revision=2, algorithm="product", definitions=CALIBRATED_DEFINITIONS,
        bands=acme["profile"].bands, horizon="6 months", status="approved", approved_by=acme["lead"].id,
        approved_at=acme["profile"].approved_at, created_by=acme["lead"].id, updated_by=acme["lead"].id,
    )
    db_session.add(revised)
    await db_session.flush()
    successor = await _successor(client, acme, profile_id=revised.id)
    await _rate(client, acme, successor, residual=(2, 2))

    comparison = (await get_json(client, acme, f"/api/rcsa/campaigns/{successor['id']}/report"))["comparison"]

    assert (comparison["comparable"], comparison["reason"]) == (False, "profile_changed")
    assert [s["status"] for s in comparison["scenarios"]] == ["not_directly_comparable"]


async def test_an_unclosed_predecessor_is_not_a_baseline(client, acme):
    successor = await _successor(client, acme)
    comparison = (await get_json(client, acme, f"/api/rcsa/campaigns/{successor['id']}/report"))["comparison"]
    assert (comparison["comparable"], comparison["reason"]) == (False, "previous_not_closed")


async def test_a_campaign_without_predecessor_has_no_comparison(client, acme):
    assert (await _report(client, acme))["comparison"] is None
