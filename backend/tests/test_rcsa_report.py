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
