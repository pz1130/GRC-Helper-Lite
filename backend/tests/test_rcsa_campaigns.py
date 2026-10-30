"""RCSA 2A：模块开关、权限、活动草稿、范围、分派、评分档案批准与激活快照。"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.audit.models import AuditEngagement
from app.controls.models import Control
from app.frameworks.models import Mapping
from app.iam.models import AuditLog
from app.iam.permissions import Role
from tests.rcsa_support import (
    CALIBRATED_DEFINITIONS,
    CAMPAIGN_BODY,
    approved_profile,
    library,
    person,
    rcsa_enabled,  # noqa: F401 — fixture
    with_key,
)

pytestmark = pytest.mark.usefixtures("rcsa_enabled")


@pytest_asyncio.fixture
async def acme(client, db_session):
    lead = await person(client, db_session, "lead", Role.GRC_LEAD)
    reviewer = await person(client, db_session, "reviewer", Role.GRC_LEAD)
    assignee = await person(client, db_session, "assignee", Role.CONTRIBUTOR)
    viewer = await person(client, db_session, "viewer", Role.VIEWER)
    lib = await library(db_session, owner=assignee.user)
    profile = await approved_profile(db_session, lead.user)
    return {
        "lead": lead, "reviewer": reviewer, "assignee": assignee, "viewer": viewer,
        "lib": lib, "profile": profile,
    }


async def _campaign(client, acme, **overrides) -> dict:
    body = CAMPAIGN_BODY | {
        "framework_id": acme["lib"].framework.id, "profile_id": acme["profile"].id,
    } | overrides
    created = await client.post("/api/rcsa/campaigns", json=body, headers=acme["lead"].headers)
    assert created.status_code == 201, created.text
    return created.json()


async def _scoped(client, acme, control_ids=None, reviewer=None) -> tuple[dict, list[dict]]:
    campaign = await _campaign(client, acme)
    ids = control_ids or [acme["lib"].controls[0].id]
    added = await client.post(
        f"/api/rcsa/campaigns/{campaign['id']}/items",
        json={"control_ids": ids, "reviewer_id": (reviewer or acme["reviewer"]).id},
        headers=acme["lead"].headers,
    )
    assert added.status_code == 201, added.text
    return campaign, added.json()


async def _activate(client, acme, campaign, key="act-1", version=None):
    return await client.post(
        f"/api/rcsa/campaigns/{campaign['id']}/activate",
        json={"expected_version": version or campaign["version"]},
        headers=with_key(acme["lead"], key),
    )


# ── 开关与准入 ──────────────────────────────────────────────


async def test_module_is_off_unless_enabled(client, db_session, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "rcsa_enabled", False)
    lead = await person(client, db_session, "lead", Role.GRC_LEAD)

    status = await client.get("/api/rcsa/status", headers=lead.headers)
    listing = await client.get("/api/rcsa/campaigns", headers=lead.headers)

    assert status.json() == {"enabled": False}
    assert listing.status_code == 404
    assert listing.json()["code"] == "rcsa.disabled"


async def test_audit_scoped_accounts_cannot_reach_rcsa(client, db_session):
    engagement = AuditEngagement(name="Acme external audit", audit_type="external", status="preparing")
    db_session.add(engagement)
    await db_session.flush()
    auditor = await person(client, db_session, "auditor", Role.VIEWER)
    auditor.user.engagement_scope_id = engagement.id
    await db_session.flush()

    listing = await client.get("/api/rcsa/campaigns", headers=auditor.headers)

    assert listing.status_code == 403
    assert listing.json()["code"] == "rcsa.scoped_account"


@pytest.mark.parametrize("who", ["viewer", "assignee"])
async def test_only_managers_create_campaigns(client, acme, who):
    body = CAMPAIGN_BODY | {"framework_id": acme["lib"].framework.id, "profile_id": acme["profile"].id}
    refused = await client.post("/api/rcsa/campaigns", json=body, headers=acme[who].headers)
    assert refused.status_code == 403


async def test_viewer_can_read_campaigns(client, acme):
    campaign = await _campaign(client, acme)
    listing = await client.get("/api/rcsa/campaigns", headers=acme["viewer"].headers)
    assert [row["id"] for row in listing.json()["items"]] == [campaign["id"]]
    assert listing.json()["total"] == 1


# ── 草稿与范围 ──────────────────────────────────────────────


async def test_campaign_period_must_not_end_before_it_starts(client, acme):
    body = CAMPAIGN_BODY | {
        "framework_id": acme["lib"].framework.id, "profile_id": acme["profile"].id,
        "period_start": "2026-03-01", "period_end": "2026-02-01",
    }
    refused = await client.post("/api/rcsa/campaigns", json=body, headers=acme["lead"].headers)
    assert refused.status_code == 422


async def test_scope_lists_each_mapped_control_once_and_skips_merged(client, db_session, acme):
    controls = acme["lib"].controls
    merged = controls[2]
    merged.status = "merged"
    merged.merged_into_id = controls[1].id
    unmapped = Control(code="C-9100", title="Unmapped", statement="Not mapped.")
    db_session.add(unmapped)
    await db_session.flush()
    campaign = await _campaign(client, acme)

    scope = await client.get(f"/api/rcsa/campaigns/{campaign['id']}/scope", headers=acme["lead"].headers)

    rows = scope.json()
    assert [row["code"] for row in rows] == ["C-9001", "C-9002"]
    assert rows[0]["framework_items"] == ["AC-1", "AC-2"]
    assert [row["included"] for row in rows] == [False, False]


async def test_added_item_defaults_to_the_control_owner_as_assignee(client, acme):
    campaign, items = await _scoped(client, acme)
    assert items[0]["assignee_id"] == acme["assignee"].id
    assert items[0]["reviewer_id"] == acme["reviewer"].id
    scope = await client.get(f"/api/rcsa/campaigns/{campaign['id']}/scope", headers=acme["lead"].headers)
    assert [row["included"] for row in scope.json()] == [True, False, False]


async def test_control_outside_the_framework_cannot_be_added(client, db_session, acme):
    outsider = Control(code="C-9100", title="Unmapped", statement="Not mapped.")
    db_session.add(outsider)
    await db_session.flush()
    campaign = await _campaign(client, acme)
    refused = await client.post(
        f"/api/rcsa/campaigns/{campaign['id']}/items", json={"control_ids": [outsider.id]},
        headers=acme["lead"].headers,
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.control_not_in_scope"


async def test_same_control_cannot_be_added_twice(client, acme):
    campaign, items = await _scoped(client, acme)
    again = await client.post(
        f"/api/rcsa/campaigns/{campaign['id']}/items",
        json={"control_ids": [items[0]["control_id"]]}, headers=acme["lead"].headers,
    )
    assert again.status_code == 409
    assert again.json()["code"] == "rcsa.item_exists"


async def test_assignee_cannot_be_their_own_reviewer(client, acme):
    _, items = await _scoped(client, acme)
    refused = await client.post(
        f"/api/rcsa/items/{items[0]['id']}/assign",
        json={"assignee_id": acme["reviewer"].id, "expected_version": items[0]["version"]},
        headers=acme["lead"].headers,
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.reviewer_is_assignee"


async def test_reviewer_needs_review_permission(client, acme):
    _, items = await _scoped(client, acme)
    refused = await client.post(
        f"/api/rcsa/items/{items[0]['id']}/assign",
        json={"reviewer_id": acme["viewer"].id, "expected_version": items[0]["version"]},
        headers=acme["lead"].headers,
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.reviewer_lacks_permission"


async def test_draft_item_can_be_removed_from_scope(client, acme):
    campaign, items = await _scoped(client, acme)
    removed = await client.delete(f"/api/rcsa/items/{items[0]['id']}", headers=acme["lead"].headers)
    assert removed.status_code == 204
    listing = await client.get(f"/api/rcsa/campaigns/{campaign['id']}/items", headers=acme["lead"].headers)
    assert listing.json() == []


# ── 评分档案 ────────────────────────────────────────────────


async def test_profile_needs_full_calibration_before_approval(client, acme):
    created = await client.post(
        "/api/rcsa/profiles", json={"key": "acme-alt"}, headers=acme["lead"].headers
    )
    assert created.status_code == 201, created.text
    profile = created.json()

    refused = await client.post(
        f"/api/rcsa/profiles/{profile['id']}/approve",
        json={"expected_version": profile["version"]}, headers=acme["lead"].headers,
    )

    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.profile_incomplete"


async def test_calibrated_profile_can_be_approved_and_is_then_immutable(client, acme):
    profile = (await client.post(
        "/api/rcsa/profiles", json={"key": "acme-alt"}, headers=acme["lead"].headers
    )).json()
    edited = await client.patch(
        f"/api/rcsa/profiles/{profile['id']}",
        json={"definitions": CALIBRATED_DEFINITIONS, "horizon": "12 months",
              "expected_version": profile["version"]},
        headers=acme["lead"].headers,
    )
    assert edited.status_code == 200, edited.text
    approved = await client.post(
        f"/api/rcsa/profiles/{profile['id']}/approve",
        json={"expected_version": edited.json()["version"]}, headers=acme["lead"].headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    refused = await client.patch(
        f"/api/rcsa/profiles/{profile['id']}",
        json={"horizon": "6 months", "expected_version": approved.json()["version"]},
        headers=acme["lead"].headers,
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.profile_immutable"


async def test_bands_with_a_gap_block_approval(client, acme):
    profile = (await client.post(
        "/api/rcsa/profiles", json={"key": "acme-alt"}, headers=acme["lead"].headers
    )).json()
    edited = (await client.patch(
        f"/api/rcsa/profiles/{profile['id']}",
        json={
            "definitions": CALIBRATED_DEFINITIONS, "horizon": "12 months",
            "bands": [{"min": 1, "max": 9, "label": "Low"}, {"min": 11, "max": 25, "label": "High"}],
            "expected_version": profile["version"],
        },
        headers=acme["lead"].headers,
    )).json()
    refused = await client.post(
        f"/api/rcsa/profiles/{profile['id']}/approve",
        json={"expected_version": edited["version"]}, headers=acme["lead"].headers,
    )
    assert refused.json()["code"] == "rcsa.profile_incomplete"


# ── 激活 ────────────────────────────────────────────────────


async def test_activation_freezes_control_mapping_and_profile(client, db_session, acme):
    campaign, items = await _scoped(client, acme)

    activated = await _activate(client, acme, campaign)

    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"
    control = acme["lib"].controls[0]
    control.title = "Renamed after activation"
    await db_session.flush()
    item = (await client.get(f"/api/rcsa/items/{items[0]['id']}", headers=acme["lead"].headers)).json()
    assert item["control_snapshot"]["title"] == "Acme control 1"
    assert [m["code"] for m in item["mapping_snapshot"]] == ["AC-1", "AC-2"]
    assert item["status"] == "pending"
    detail = (await client.get(f"/api/rcsa/campaigns/{campaign['id']}", headers=acme["lead"].headers)).json()
    assert detail["profile_snapshot"]["horizon"] == "12 months"
    assert detail["framework_snapshot"]["key"] == "acme-it"
    audit = await db_session.scalar(select(AuditLog).where(AuditLog.action == "rcsa.activate"))
    assert audit.after["status"] == "active"


async def test_activation_rechecks_scope_instead_of_trusting_creation(client, db_session, acme):
    controls = acme["lib"].controls
    campaign, _ = await _scoped(client, acme, control_ids=[controls[0].id, controls[1].id])
    controls[0].status = "merged"
    controls[0].merged_into_id = controls[2].id
    for mapping in await db_session.scalars(select(Mapping).where(Mapping.control_id == controls[1].id)):
        await db_session.delete(mapping)
    acme["assignee"].user.is_active = False
    await db_session.flush()

    refused = await _activate(client, acme, campaign)

    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.activation_blocked"
    blockers = {(b["code"], b.get("control_code")) for b in refused.json()["params"]["blockers"]}
    assert ("rcsa.blocked.control_merged", "C-9001") in blockers
    assert ("rcsa.blocked.control_unmapped", "C-9002") in blockers
    assert ("rcsa.blocked.assignee_inactive", "C-9001") in blockers


async def test_activation_needs_an_approved_profile_and_a_reviewer(client, db_session, acme):
    acme["profile"].status = "draft"
    acme["profile"].approved_by = None
    acme["profile"].approved_at = None
    await db_session.flush()
    campaign = await _campaign(client, acme)
    await client.post(
        f"/api/rcsa/campaigns/{campaign['id']}/items",
        json={"control_ids": [acme["lib"].controls[0].id]}, headers=acme["lead"].headers,
    )

    refused = await _activate(client, acme, campaign)

    codes = {b["code"] for b in refused.json()["params"]["blockers"]}
    assert {"rcsa.blocked.profile_not_approved", "rcsa.blocked.reviewer_missing"} <= codes


async def test_empty_scope_cannot_be_activated(client, acme):
    campaign = await _campaign(client, acme)
    refused = await _activate(client, acme, campaign)
    assert {b["code"] for b in refused.json()["params"]["blockers"]} == {"rcsa.blocked.scope_empty"}


async def test_stale_version_is_a_conflict(client, acme):
    campaign, _ = await _scoped(client, acme)
    refused = await _activate(client, acme, campaign, version=campaign["version"] + 5)
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.version_conflict"


async def test_repeated_activation_with_the_same_key_replays(client, db_session, acme):
    campaign, _ = await _scoped(client, acme)
    first = await _activate(client, acme, campaign, key="same")
    again = await _activate(client, acme, campaign, key="same")

    assert again.status_code == 200
    assert again.json() == first.json()
    activations = list(await db_session.scalars(select(AuditLog).where(AuditLog.action == "rcsa.activate")))
    assert len(activations) == 1


async def test_reusing_a_key_with_a_different_body_is_a_conflict(client, acme):
    campaign, _ = await _scoped(client, acme)
    await _activate(client, acme, campaign, key="same")
    reused = await _activate(client, acme, campaign, key="same", version=campaign["version"] + 1)
    assert reused.status_code == 409
    assert reused.json()["code"] == "rcsa.idempotency_key_reused"


async def test_one_shot_commands_need_an_idempotency_key(client, acme):
    campaign, _ = await _scoped(client, acme)
    refused = await client.post(
        f"/api/rcsa/campaigns/{campaign['id']}/activate",
        json={"expected_version": campaign["version"]}, headers=acme["lead"].headers,
    )
    assert refused.status_code == 400
    assert refused.json()["code"] == "rcsa.idempotency_key_required"


async def test_scope_is_fixed_after_activation(client, acme):
    campaign, _ = await _scoped(client, acme)
    await _activate(client, acme, campaign)
    refused = await client.post(
        f"/api/rcsa/campaigns/{campaign['id']}/items",
        json={"control_ids": [acme["lib"].controls[1].id]}, headers=acme["lead"].headers,
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.campaign_not_draft"


async def test_reassignment_after_activation_needs_a_reason_and_is_audited(client, db_session, acme):
    campaign, items = await _scoped(client, acme)
    await _activate(client, acme, campaign)
    other = await person(client, db_session, "backup", Role.CONTRIBUTOR)
    item = (await client.get(f"/api/rcsa/items/{items[0]['id']}", headers=acme["lead"].headers)).json()

    refused = await client.post(
        f"/api/rcsa/items/{item['id']}/assign",
        json={"assignee_id": other.id, "expected_version": item["version"]},
        headers=acme["lead"].headers,
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.reason_required"

    moved = await client.post(
        f"/api/rcsa/items/{item['id']}/assign",
        json={"assignee_id": other.id, "reason": "Owner on leave", "expected_version": item["version"]},
        headers=acme["lead"].headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["assignee_id"] == other.id
    assert moved.json()["control_snapshot"]["owner_user_id"] == acme["assignee"].id
    audit = await db_session.scalar(select(AuditLog).where(AuditLog.action == "rcsa.assign"))
    assert audit.after["reason"] == "Owner on leave"
