"""RCSA 2D：活动从激活到关闭——进入复核、退回、关闭快照、取消，以及关闭后的跟进。"""

from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update

from app.iam.permissions import Role
from app.rcsa.models import RcsaAction, RcsaItemRisk
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


# ── 全流程 ──────────────────────────────────────────────────


async def test_campaign_runs_from_activation_to_close(client, acme):
    found = await finish_items(client, acme)

    in_review = await transition(client, acme, "begin-review")
    assert in_review.status_code == 200, in_review.text
    blocked = await transition(client, acme, "close")
    assert blocked.status_code == 409
    assert {b["code"] for b in blocked.json()["params"]["blockers"]} == {"rcsa.blocked.disposition_unapproved"}

    await approve_disposition(client, acme, found["risk"])
    closed = await transition(client, acme, "close")

    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"
    report = await get_json(client, acme, f"/api/rcsa/campaigns/{acme['campaign']['id']}/closed-snapshot")
    statuses = [item["status"] for item in report["items"]]
    assert statuses == ["approved", "approved", "excluded"]
    assert report["items"][0]["response"]["revision"] == 2
    assert [r["decision"] for r in report["items"][0]["reviews"]] == ["return", "approve"]
    assert report["items"][1]["response"]["answer"]["operating"]["rating"] == "not_assessed"
    assert report["items"][2]["exclusion"]["reason"] == "Decommissioned in January"
    assert report["items"][0]["risks"][0]["disposition"] == "reduce"
    assert report["items"][0]["risks"][0]["actions"][0]["status"] == "open"
    assert report["open_actions"] == 1


async def test_follow_up_after_close_does_not_rewrite_the_closed_snapshot(client, acme):
    found = await finish_items(client, acme)
    await transition(client, acme, "begin-review")
    await approve_disposition(client, acme, found["risk"])
    assert (await transition(client, acme, "close")).status_code == 200

    action = found["action"]
    ready = await client.patch(f"/api/rcsa/actions/{action['id']}", json={
        "status": "pending_verification", "implementation_evidence": [{"description": "Feed live"}],
        "expected_version": action["version"]}, headers=acme["assignee"].headers)
    assert ready.status_code == 200, ready.text
    verified = await client.post(f"/api/rcsa/actions/{action['id']}/verify",
                                 json={"decision": "pass", "result": "0 of 25", "expected_version": ready.json()["version"]},
                                 headers=with_key(acme["reviewer"], "v"))
    assert verified.json()["status"] == "verified"

    report = await get_json(client, acme, f"/api/rcsa/campaigns/{acme['campaign']['id']}/closed-snapshot")
    assert report["items"][0]["risks"][0]["actions"][0]["status"] == "open"


async def test_closed_campaign_is_read_only(client, acme):
    found = await finish_items(client, acme)
    await transition(client, acme, "begin-review")
    await approve_disposition(client, acme, found["risk"])
    await transition(client, acme, "close")
    refused = await transition(client, acme, "cancel", {"reason": "Too late"})
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.campaign_state"


# ── 转换的前提 ──────────────────────────────────────────────


async def test_review_phase_waits_for_every_item(client, acme):
    refused = await transition(client, acme, "begin-review")
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.items_unfinished"
    assert refused.json()["params"]["count"] == 3


async def test_only_the_owner_moves_thecampaign_now(client, acme):
    refused = await transition(client, acme, "begin-review", who=acme["approver"])
    assert refused.status_code == 403
    assert refused.json()["code"] == "rcsa.not_campaign_owner"


async def test_return_to_assessment_needs_a_reason(client, acme):
    found = await finish_items(client, acme)
    assert found
    await transition(client, acme, "begin-review")
    refused = await transition(client, acme, "return", {"reason": ""})
    assert refused.status_code == 422
    back = await transition(client, acme, "return", {"reason": "Scope question from audit"})
    assert back.json()["status"] == "active"
    assert back.json()["status_reason"] == "Scope question from audit"


async def test_expired_acceptance_blocks_closing(client, db_session, acme):
    found = await finish_items(client, acme)
    risk = await approve_disposition(client, acme, found["risk"])
    await db_session.execute(update(RcsaItemRisk).where(RcsaItemRisk.id == risk["id"]).values(
        disposition="accept", accept_until=date.today() - timedelta(days=1),
        reassess_at=date.today() - timedelta(days=10),
        disposition_details={"rationale": "r", "conditions": "c"},
    ))
    await transition(client, acme, "begin-review")

    refused = await transition(client, acme, "close")

    assert "rcsa.blocked.acceptance_expired" in {b["code"] for b in refused.json()["params"]["blockers"]}


async def test_overdue_actions_need_the_owners_acknowledgement(client, db_session, acme):
    found = await finish_items(client, acme)
    await approve_disposition(client, acme, found["risk"])
    await db_session.execute(update(RcsaAction).where(RcsaAction.id == found["action"]["id"]).values(
        due_at=date.today() - timedelta(days=3)))
    await transition(client, acme, "begin-review")

    refused = await transition(client, acme, "close")
    assert {b["code"] for b in refused.json()["params"]["blockers"]} == {"rcsa.blocked.overdue_unacknowledged"}

    closed = await transition(client, acme, "close", {"overdue_acknowledgement": "Vendor delay; new date agreed"})
    assert closed.status_code == 200, closed.text
    report = await get_json(client, acme, f"/api/rcsa/campaigns/{acme['campaign']['id']}/closed-snapshot")
    assert report["acknowledgements"]["overdue"] == "Vendor delay; new date agreed"


async def test_cancelling_needs_a_reason_and_keeps_actions_alive(client, acme):
    found = await finish_items(client, acme)
    refused = await transition(client, acme, "cancel", {"reason": " "})
    assert refused.status_code == 422
    cancelled = await transition(client, acme, "cancel", {"reason": "Superseded by the Q2 campaign"})
    assert cancelled.json()["status"] == "cancelled"

    work = (await client.get("/api/rcsa/my-work", headers=acme["assignee"].headers)).json()
    assert ("action", found["action"]["id"]) in {(row["kind"], row["id"]) for row in work["rows"]}


async def test_draft_campaign_can_be_cancelled_too(client, acme):
    body = {"name": "Spare", "framework_id": acme["lib"].framework.id, "profile_id": acme["profile"].id,
            "period_start": "2026-01-01", "period_end": "2026-03-31", "due_at": "2026-04-30"}
    spare = (await client.post("/api/rcsa/campaigns", json=body, headers=acme["lead"].headers)).json()
    cancelled = await client.post(f"/api/rcsa/campaigns/{spare['id']}/cancel",
                                  json={"reason": "Created by mistake", "expected_version": spare["version"]},
                                  headers=acme["lead"].headers)
    assert cancelled.json()["status"] == "cancelled"
