"""指标阈值、重复观测、自动事件、范围与不可变历史。"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.environment.models import Implementation
from app.rcsa.models import RcsaKriObservation, RcsaTrigger
from tests.rcsa_support import (
    CAMPAIGN_BODY,
    acme_world,
    rcsa_enabled,  # noqa: F401
)
from tests.test_rcsa_triggers import _step

pytestmark = pytest.mark.usefixtures("rcsa_enabled")


@pytest_asyncio.fixture
async def world(client, db_session):
    return await acme_world(client, db_session)


def rule_body(world, **changes):
    return {"name": "Acme overdue reviews", "source": "Access feed", "unit": "count",
            "control_id": world["lib"].controls[0].id, "direction": "high",
            "amber_threshold": "5", "red_threshold": "10", "owner_id": world["assignee"].id,
            "decision_days": 3} | changes


async def rule(client, world, **changes):
    response = await client.post("/api/rcsa/kris", json=rule_body(world, **changes), headers=world["lead"].headers)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["enabled"] is False and created["latest"] is None
    enabled = await client.patch(f"/api/rcsa/kris/{created['id']}", headers=world["lead"].headers,
        json={"expected_version": created["version"], "enabled": True, "owner_id": created["owner_id"]})
    assert enabled.status_code == 200, enabled.text
    return enabled.json()


def observation(index, value):
    return {"external_id": f"obs-{index}", "value": str(value), "source_ref": f"ACME-{index}",
            "observed_at": f"2026-01-{index:02d}T12:00:00Z"}


async def observe(client, world, kri, index, value):
    return await client.post(f"/api/rcsa/kris/{kri['id']}/observations",
                             json=observation(index, value), headers=world["lead"].headers)


async def test_red_episodes_create_once_recovery_rearms_without_closing_events(client, db_session, world):
    kri = await rule(client, world)
    results = []
    for index, value in enumerate((4, 5, 10, 12, 4, 11), 1):
        response = await observe(client, world, kri, index, value)
        assert response.status_code == 200, response.text
        results.append(response.json())
    assert [r["band"] for r in results] == ["green", "amber", "red", "red", "green", "red"]
    assert results[2]["trigger_id"] == results[3]["trigger_id"]
    assert results[2]["trigger_id"] != results[5]["trigger_id"]
    triggers = list(await db_session.scalars(select(RcsaTrigger).where(RcsaTrigger.origin_kri_id == kri["id"])))
    assert len(triggers) == 2
    assert all(t.status == "registered" and t.owner_id == world["assignee"].id for t in triggers)
    assert all(t.decision_due_at == datetime.now(UTC).date() + timedelta(days=3) for t in triggers)
    assert results[0]["rule_snapshot"]["source"] == "Access feed"
    listing = await client.get("/api/rcsa/kris", headers=world["viewer"].headers)
    assert listing.json()["items"][0]["latest"]["id"] == results[-1]["id"]
    history = await client.get(f"/api/rcsa/kris/{kri['id']}/observations?limit=2&offset=2", headers=world["viewer"].headers)
    assert history.json()["total"] == 6
    assert [r["external_id"] for r in history.json()["items"]] == ["obs-4", "obs-3"]


async def test_idempotency_and_disabled_replays(client, db_session, world):
    kri = await rule(client, world)
    first = await observe(client, world, kri, 1, 10)
    replay = await observe(client, world, kri, 1, 10)
    assert replay.json() == first.json()
    conflict = await observe(client, world, kri, 1, 11)
    assert conflict.status_code == 409 and conflict.json()["code"] == "rcsa.kri_replay_conflict"
    disabled = await client.patch(f"/api/rcsa/kris/{kri['id']}", headers=world["lead"].headers,
        json={"expected_version": kri["version"], "enabled": False, "owner_id": kri["owner_id"]})
    assert disabled.status_code == 200
    assert (await observe(client, world, kri, 1, 10)).json() == first.json()
    assert (await observe(client, world, kri, 2, 10)).json()["code"] == "rcsa.kri_disabled"
    assert await db_session.scalar(select(func.count()).select_from(RcsaKriObservation)) == 1


async def test_lower_is_worse_and_exact_thresholds(client, world):
    kri = await rule(client, world, direction="low", amber_threshold="95", red_threshold="90")
    for index, (value, band) in enumerate(((96, "green"), (95, "amber"), (90, "red")), 1):
        response = await observe(client, world, kri, index, value)
        assert response.status_code == 200, response.text
        assert response.json()["band"] == band


@pytest.mark.parametrize("change", [{"amber_threshold": "10"}, {"direction": "low"},
    {"red_threshold": "NaN"}, {"red_threshold": "Infinity"}, {"red_threshold": "0.1234567"},
    {"red_threshold": "100000000000000"}, {"decision_days": 0}])
async def test_invalid_definitions_rejected(client, world, change):
    response = await client.post("/api/rcsa/kris", json=rule_body(world, **change), headers=world["lead"].headers)
    assert response.status_code == 422, response.text


async def test_bad_time_and_values_do_not_append(client, db_session, world):
    kri = await rule(client, world)
    assert (await observe(client, world, kri, 2, 1)).status_code == 200
    assert (await observe(client, world, kri, 1, 10)).json()["code"] == "rcsa.kri_out_of_order"
    for changes, status in [({"observed_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}, 409),
                             ({"observed_at": "2026-01-03T12:00:00"}, 422), ({"value": "NaN"}, 422)]:
        response = await client.post(f"/api/rcsa/kris/{kri['id']}/observations", headers=world["lead"].headers,
                                      json=observation(3, 10) | changes)
        assert response.status_code == status, response.text
    assert await db_session.scalar(select(func.count()).select_from(RcsaKriObservation)) == 1


async def test_only_managers_can_configure_and_ingest(client, world):
    kri = await rule(client, world)
    for who in ("viewer", "assignee"):
        assert (await client.post("/api/rcsa/kris", json=rule_body(world), headers=world[who].headers)).status_code == 403
        response = await client.post(f"/api/rcsa/kris/{kri['id']}/observations",
                                     json=observation(1, 10), headers=world[who].headers)
        assert response.status_code == 403


async def test_definition_and_observations_are_immutable_in_database(client, db_session, world):
    kri = await rule(client, world)
    obs = (await observe(client, world, kri, 1, 10)).json()
    for sql in (f"UPDATE rcsa_kris SET red_threshold = 11 WHERE id = {kri['id']}",
                f"UPDATE rcsa_kri_observations SET value = 1 WHERE id = {obs['id']}",
                f"DELETE FROM rcsa_kri_observations WHERE id = {obs['id']}"):
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await db_session.execute(text(sql))


async def test_implementation_event_matches_only_its_target(client, db_session, world):
    control = world["lib"].controls[0]
    impl = Implementation(control_id=control.id, description="Acme process", how_enforced="manual", status="implemented")
    db_session.add(impl)
    await db_session.flush()
    kri = await rule(client, world, implementation_id=impl.id)
    obs = (await observe(client, world, kri, 1, 10)).json()
    trigger = (await client.get(f"/api/rcsa/triggers/{obs['trigger_id']}", headers=world["lead"].headers)).json()
    assert trigger["controls"][0]["implementation_id"] == impl.id
    changed = await client.patch(f"/api/rcsa/triggers/{trigger['id']}", headers=world["lead"].headers,
                                 json={"expected_version": trigger["version"], "controls": []})
    assert changed.status_code == 409 and changed.json()["code"] == "rcsa.kri_trigger_scope_frozen"
    triaged = (await _step(client, world["assignee"], trigger, "triage")).json()
    awaiting = (await _step(client, world["assignee"], triaged, "decide", {"decision": "reassess"})).json()
    for scoped in (False, True):
        campaign = (await client.post("/api/rcsa/campaigns", headers=world["lead"].headers, json=CAMPAIGN_BODY | {
            "framework_id": world["lib"].framework.id, "profile_id": world["profile"].id})).json()
        added = await client.post(f"/api/rcsa/campaigns/{campaign['id']}/items", headers=world["lead"].headers,
            json={"implementation_ids": [impl.id]} if scoped else {"control_ids": [control.id]})
        assert added.status_code == 201
        linked = await _step(client, world["assignee"], awaiting, "link", {"campaign_id": campaign["id"]})
        assert linked.status_code == (200 if scoped else 409), linked.text
    from app.errors import Conflict
    from app.rcsa.triggers import complete_for_campaign

    # 关联草稿后，活动即使没有真正完成该对象的评估，也不能把事件自动关掉。
    with pytest.raises(Conflict):
        await complete_for_campaign(db_session, campaign["id"], world["lead"].user)
    deletion = await client.delete(f"/api/implementations/{impl.id}", headers=world["lead"].headers)
    assert deletion.status_code == 409


async def test_disabled_owner_can_be_reassigned_before_retry(client, db_session, world):
    kri = await rule(client, world)
    world["assignee"].user.is_active = False
    await db_session.flush()
    refused = await observe(client, world, kri, 1, 10)
    assert refused.status_code == 409 and refused.json()["code"] == "rcsa.person_unavailable"
    assert await db_session.scalar(select(func.count()).select_from(RcsaKriObservation)) == 0
    changed = await client.patch(f"/api/rcsa/kris/{kri['id']}", headers=world["lead"].headers,
        json={"expected_version": kri["version"], "enabled": True, "owner_id": world["lead"].id})
    assert changed.status_code == 200
    assert (await observe(client, world, kri, 1, 10)).status_code == 200


async def test_module_disabled_and_audit_scoped_users_cannot_ingest(client, db_session, world, monkeypatch):
    from app.audit.models import AuditEngagement
    from app.config import get_settings

    kri = await rule(client, world)
    engagement = AuditEngagement(name="Acme audit", audit_type="external", status="preparing")
    db_session.add(engagement)
    await db_session.flush()
    world["lead"].user.engagement_scope_id = engagement.id
    await db_session.flush()
    assert (await observe(client, world, kri, 1, 10)).status_code == 403
    world["lead"].user.engagement_scope_id = None
    await db_session.flush()
    monkeypatch.setattr(get_settings(), "rcsa_enabled", False)
    assert (await observe(client, world, kri, 1, 10)).status_code == 404
