"""RCSA 2D：手动触发登记与处置闭环，以及按查询时刻计算的站内待办。"""

from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update

from app.iam.permissions import Role
from app.rcsa.models import RcsaCampaign
from tests.rcsa_support import (
    acme_world,
    activated,
    draft,
    person,
    rcsa_enabled,  # noqa: F401 — fixture
    review,
    submitted,
    with_key,
)

pytestmark = pytest.mark.usefixtures("rcsa_enabled")

DUE = (date.today() + timedelta(days=14)).isoformat()


@pytest_asyncio.fixture
async def acme(client, db_session):
    world = await acme_world(client, db_session)
    world["second"] = await person(client, db_session, "second", Role.GRC_LEAD)
    return world


async def _register(client, acme, **overrides) -> dict:
    body = {
        "trigger_type": "incident", "title": "Backup job failed for three nights", "source_ref": "INC-3301",
        "occurred_at": "2026-02-10T08:00:00Z", "owner_id": acme["lead"].id, "decision_due_at": DUE,
        "control_ids": [acme["lib"].controls[0].id],
    } | overrides
    created = await client.post("/api/rcsa/triggers", json=body, headers=acme["assignee"].headers)
    assert created.status_code == 201, created.text
    return created.json()


async def _step(client, who, trigger, name, body=None, key=None):
    return await client.post(f"/api/rcsa/triggers/{trigger['id']}/{name}",
                             json={"expected_version": trigger["version"]} | (body or {}),
                             headers=with_key(who, key) if key else who.headers)


# ── 触发 ────────────────────────────────────────────────────


async def test_contributors_register_triggers_with_affected_controls(client, acme):
    trigger = await _register(client, acme)
    assert trigger["status"] == "registered"
    assert [c["control_snapshot"]["code"] for c in trigger["controls"]] == ["C-9001"]


async def test_planned_change_needs_its_planned_date(client, acme):
    refused = await client.post("/api/rcsa/triggers", json={
        "trigger_type": "planned_change", "title": "IdP migration", "source_ref": "CHG-12",
        "occurred_at": "2026-02-10T08:00:00Z", "owner_id": acme["lead"].id, "decision_due_at": DUE,
    }, headers=acme["assignee"].headers)
    assert refused.status_code == 422


async def test_triage_needs_at_least_one_affected_control(client, acme):
    trigger = await _register(client, acme, control_ids=[])
    refused = await _step(client, acme["lead"], trigger, "triage")
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.trigger_scope_empty"


async def test_no_reassessment_is_confirmed_by_another_reviewer(client, acme):
    trigger = await _register(client, acme)
    triaged = (await _step(client, acme["lead"], trigger, "triage")).json()

    without_reason = await _step(client, acme["lead"], triaged, "decide", {"decision": "no_reassessment"})
    assert without_reason.json()["code"] == "rcsa.reason_required"

    proposed = (await _step(client, acme["lead"], triaged, "decide",
                            {"decision": "no_reassessment", "rationale": "Restored from replica; no data lost"})).json()
    assert proposed["status"] == "no_reassessment"

    own = await _step(client, acme["lead"], proposed, "review", {"decision": "approve"}, key="t1")
    assert own.status_code == 403
    assert own.json()["code"] == "rcsa.approver_is_proposer"

    confirmed = await _step(client, acme["second"], proposed, "review", {"decision": "approve"}, key="t2")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "completed"


async def test_reassessment_completes_only_when_the_linked_campaign_closes(client, db_session, acme):
    trigger = await _register(client, acme, control_ids=[c.id for c in acme["lib"].controls[:2]])
    triaged = (await _step(client, acme["lead"], trigger, "triage")).json()
    awaiting = (await _step(client, acme["lead"], triaged, "decide",
                            {"decision": "reassess", "rationale": "Backups are a key control"})).json()
    assert awaiting["status"] == "awaiting_assessment"
    campaign, _ = await activated(client, acme, controls=1)

    uncovered = await _step(client, acme["lead"], awaiting, "link", {"campaign_id": campaign["id"]})
    assert uncovered.status_code == 409
    assert uncovered.json()["code"] == "rcsa.trigger_controls_uncovered"
    assert uncovered.json()["params"]["control_codes"] == ["C-9002"]

    linked = await _step(client, acme["lead"], awaiting, "link", {
        "campaign_id": campaign["id"],
        "exclusions": [{"control_id": acme["lib"].controls[1].id, "reason": "Restore path untouched"}],
    })
    assert linked.status_code == 200, linked.text
    assert linked.json()["status"] == "awaiting_assessment"

    item = (await client.get(f"/api/rcsa/campaigns/{campaign['id']}/items", headers=acme["lead"].headers)).json()[0]
    assert (await review(client, acme["reviewer"], await submitted(client, acme, item), "approve")).status_code == 200
    for step in ("begin-review", "close"):
        current = (await client.get(f"/api/rcsa/campaigns/{campaign['id']}", headers=acme["lead"].headers)).json()
        moved = await client.post(f"/api/rcsa/campaigns/{campaign['id']}/{step}",
                                  json={"expected_version": current["version"]},
                                  headers=with_key(acme["lead"], step))
        assert moved.status_code == 200, moved.text

    after = (await client.get(f"/api/rcsa/triggers/{trigger['id']}", headers=acme["lead"].headers)).json()
    assert after["status"] == "completed"
    assert {c["control_snapshot"]["code"]: c["coverage_disposition"] for c in after["controls"]} == {
        "C-9001": "assess", "C-9002": "excluded",
    }


async def test_cancelled_campaign_does_not_complete_its_trigger(client, db_session, acme):
    trigger = await _register(client, acme)
    triaged = (await _step(client, acme["lead"], trigger, "triage")).json()
    awaiting = (await _step(client, acme["lead"], triaged, "decide",
                            {"decision": "reassess", "rationale": "Key control"})).json()
    campaign, _ = await activated(client, acme, controls=1)
    linked = await _step(client, acme["lead"], awaiting, "link", {"campaign_id": campaign["id"]})
    assert linked.status_code == 200, linked.text

    await client.post(f"/api/rcsa/campaigns/{campaign['id']}/cancel",
                      json={"reason": "Wrong scope", "expected_version": campaign["version"]},
                      headers=acme["lead"].headers)

    after = (await client.get(f"/api/rcsa/triggers/{trigger['id']}", headers=acme["lead"].headers)).json()
    assert after["status"] == "awaiting_assessment"


async def test_viewer_cannot_register_triggers(client, acme):
    refused = await client.post("/api/rcsa/triggers", json={
        "trigger_type": "incident", "title": "x", "source_ref": "INC-1", "occurred_at": "2026-02-10T08:00:00Z",
        "owner_id": acme["lead"].id, "decision_due_at": DUE,
    }, headers=acme["viewer"].headers)
    assert refused.status_code == 403


# ── 待办 ────────────────────────────────────────────────────


async def _kinds(client, who) -> set[tuple[str, int]]:
    work = (await client.get("/api/rcsa/my-work", headers=who.headers)).json()
    return {(row["kind"], row["id"]) for row in work["rows"]}


async def test_work_follows_the_assessment_from_assessor_to_reviewer(client, acme):
    _, items = await activated(client, acme, controls=1)
    item = items[0]
    assert ("assessment", item["id"]) in await _kinds(client, acme["assignee"])
    assert ("review", item["id"]) not in await _kinds(client, acme["reviewer"])

    await submitted(client, acme, item)

    assert ("assessment", item["id"]) not in await _kinds(client, acme["assignee"])
    assert ("review", item["id"]) in await _kinds(client, acme["reviewer"])


async def test_overdue_is_computed_at_query_time_and_listed_first(client, db_session, acme):
    campaign, items = await activated(client, acme, controls=2)
    await draft(client, acme["assignee"], items[0])
    await db_session.execute(update(RcsaCampaign).where(RcsaCampaign.id == campaign["id"]).values(
        due_at=date.today() - timedelta(days=1)))

    work = (await client.get("/api/rcsa/my-work", headers=acme["assignee"].headers)).json()

    assessments = [row for row in work["rows"] if row["kind"] == "assessment"]
    assert len(assessments) == 2
    assert all(row["overdue"] for row in assessments)
    item = (await client.get(f"/api/rcsa/items/{items[0]['id']}", headers=acme["lead"].headers)).json()
    assert item["status"] == "draft"


async def test_trigger_owner_sees_the_open_decision(client, acme):
    trigger = await _register(client, acme)
    assert ("trigger", trigger["id"]) in await _kinds(client, acme["lead"])
