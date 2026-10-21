"""同一控制的不同系统分别评估；范围互斥、历史快照与实施记录引用保护。"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.environment.models import Implementation, TechAsset
from app.rcsa.report import _scenarios
from tests.rcsa_support import (
    CAMPAIGN_BODY,
    COMPLETE_ANSWER,
    acme_world,
    draft,
    rcsa_enabled,  # noqa: F401
    review,
    save,
    submit,
    with_key,
)

pytestmark = pytest.mark.usefixtures("rcsa_enabled")


@pytest_asyncio.fixture
async def scoped(client, db_session):
    world = await acme_world(client, db_session)
    assets = [TechAsset(name=f"Acme {name}", category="database", environment="prod", status="active")
              for name in ("Ledger", "Directory")]
    db_session.add_all(assets)
    await db_session.flush()
    implementations = [Implementation(control_id=world["lib"].controls[0].id, tech_asset_id=a.id,
        description=f"Review {a.name}", how_enforced="manual", status="implemented",
        owner_user_id=world["assignee"].id) for a in assets]
    db_session.add_all(implementations)
    await db_session.flush()
    created = await client.post("/api/rcsa/campaigns", headers=world["lead"].headers, json=CAMPAIGN_BODY | {
        "framework_id": world["lib"].framework.id, "profile_id": world["profile"].id})
    assert created.status_code == 201, created.text
    return world | {"campaign": created.json(), "assets": assets, "implementations": implementations}


async def _add(client, scoped, **body):
    return await client.post(f"/api/rcsa/campaigns/{scoped['campaign']['id']}/items",
        headers=scoped["lead"].headers, json={"reviewer_id": scoped["reviewer"].id} | body)


async def _activate(client, scoped):
    campaign = scoped["campaign"]
    activated = await client.post(f"/api/rcsa/campaigns/{campaign['id']}/activate",
        headers=with_key(scoped["lead"], "activate-scoped"), json={"expected_version": campaign["version"]})
    assert activated.status_code == 200, activated.text
    items = await client.get(f"/api/rcsa/campaigns/{campaign['id']}/items", headers=scoped["lead"].headers)
    return items.json()


async def test_separate_answers_and_frozen_systems(client, db_session, scoped):
    added = await _add(client, scoped, implementation_ids=[i.id for i in scoped["implementations"]])
    assert added.status_code == 201, added.text
    assert len(added.json()) == 2
    items = await _activate(client, scoped)
    for index, item in enumerate(items):
        answer = COMPLETE_ANSWER | {"limitations": f"System limitation {index}", "no_risk_rationale": ""}
        response = await save(client, scoped["assignee"], await draft(client, scoped["assignee"], item), answer)
        risk = await client.post(f"/api/rcsa/items/{item['id']}/risks", headers=scoped["assignee"].headers, json={
            "scenario_key": "access", "cause": "Manual process", "event": "Access retained", "impact": "Data loss",
            "inherent_likelihood": 4, "inherent_impact": 4, "inherent_rationale": "High exposure",
            "residual_likelihood": index + 1, "residual_impact": 2, "residual_rationale": "System review",
            "owner_id": scoped["assignee"].id,
        })
        assert risk.status_code == 201, risk.text
        response = (await client.get(f"/api/rcsa/responses/{response['id']}", headers=scoped["assignee"].headers)).json()
        submitted = await submit(client, scoped["assignee"], response, key=f"scope-submit-{index}")
        assert submitted.status_code == 200, submitted.text
        approved = await review(client, scoped["reviewer"], submitted.json(), "approve")
        assert approved.status_code == 200, approved.text
    scoped["assets"][0].name = "Renamed system"
    scoped["implementations"][0].description = "Changed later"
    await db_session.flush()
    report = await client.get(f"/api/rcsa/campaigns/{scoped['campaign']['id']}/report", headers=scoped["lead"].headers)
    assert report.status_code == 200, report.text
    assert report.json()["scope"]["approved"] == 2
    assert [r["scenarios"][0]["residual_score"] for r in report.json()["items"]] == [2, 4]
    assert {r["target_label"] for r in report.json()["items"]} == {"Acme Ledger · prod", "Acme Directory · prod"}
    assert {r["text"] for r in report.json()["limitations"]} == {"System limitation 0", "System limitation 1"}


@pytest.mark.parametrize("whole_first", [False, True])
async def test_scope_overlap_is_rejected_but_sibling_implementations_are_allowed(client, scoped, whole_first):
    whole = {"control_ids": [scoped["lib"].controls[0].id]}
    one = {"implementation_ids": [scoped["implementations"][0].id]}
    assert (await _add(client, scoped, **(whole if whole_first else one))).status_code == 201
    collision = await _add(client, scoped, **(one if whole_first else whole))
    assert collision.status_code == 409
    assert collision.json()["code"] == "rcsa.item_exists"
    rows = (await client.get(f"/api/rcsa/campaigns/{scoped['campaign']['id']}/scope?basis=implementation",
        headers=scoped["lead"].headers)).json()
    assert rows[0]["included"] is True
    assert rows[1]["included"] is whole_first
    if not whole_first:
        assert (await _add(client, scoped, implementation_ids=[scoped["implementations"][1].id])).status_code == 201


async def test_referenced_implementation_cannot_move_or_disappear(client, db_session, scoped):
    impl = scoped["implementations"][0]
    assert (await _add(client, scoped, implementation_ids=[impl.id])).status_code == 201
    for method, body in [("PATCH", {"tech_asset_id": None}), ("DELETE", None)]:
        response = await client.request(method, f"/api/implementations/{impl.id}", json=body,
                                        headers=scoped["lead"].headers)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "rcsa.implementation_in_use"
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(text("UPDATE implementations SET tech_asset_id = NULL WHERE id = :id"), {"id": impl.id})


async def test_unknown_or_ambiguous_scope_is_rejected(client, scoped):
    assert (await _add(client, scoped, implementation_ids=[999999])).status_code == 404
    assert (await _add(client, scoped, implementation_ids=[scoped["implementations"][0].id],
                       control_ids=[scoped["lib"].controls[0].id])).status_code == 422
    assert (await _add(client, scoped)).status_code == 422


def test_comparison_keeps_same_scenario_on_different_systems_separate():
    risk = {"scenario_key": "access", "residual_score": 6}
    items = [{"control": {"id": 1, "code": "C-1"}, "implementation_id": i, "risks": [risk]}
             for i in (None, 10, 11)]
    scenarios = _scenarios(items)
    assert set(scenarios) == {(1, None, "access"), (1, 10, "access"), (1, 11, "access")}
    assert set(_scenarios([{ "control": {"id": 1}, "risks": [risk]}])) == {(1, None, "access")}


async def test_scoped_campaign_does_not_claim_whole_control_trigger_coverage(client, scoped):
    from tests.test_rcsa_triggers import _register, _step

    assert (await _add(client, scoped, implementation_ids=[scoped["implementations"][0].id])).status_code == 201
    trigger = await _register(client, scoped)
    triaged = (await _step(client, scoped["lead"], trigger, "triage")).json()
    awaiting = (await _step(client, scoped["lead"], triaged, "decide",
                            {"decision": "reassess", "rationale": "Assess control coverage"})).json()
    linked = await _step(client, scoped["lead"], awaiting, "link", {"campaign_id": scoped["campaign"]["id"]})
    assert linked.status_code == 409, linked.text
    assert linked.json()["code"] == "rcsa.trigger_controls_uncovered"


async def test_merge_cannot_retarget_a_scoped_implementation(client, db_session, scoped):
    from app.controls.merge import plan_merge
    from app.errors import Conflict
    from app.review.materialize import merge_controls

    assert (await _add(client, scoped, implementation_ids=[scoped["implementations"][0].id])).status_code == 201
    loser, winner = scoped["lib"].controls[:2]
    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)
    assert "merge.blocked.rcsa_implementation" in {b.code for b in plan.blockers}
    with pytest.raises(Conflict, match="RCSA"):
        await merge_controls(db_session, loser_id=loser.id, winner_id=winner.id, actor=scoped["lead"].user)


async def test_implementation_owner_is_not_an_independent_tester(client, db_session, scoped):
    from tests.test_rcsa_responses import _attach

    scoped["lib"].controls[0].owner_user_id = scoped["lead"].id
    await db_session.flush()
    added = await _add(client, scoped, implementation_ids=[scoped["implementations"][0].id],
                       assignee_id=scoped["lead"].id)
    assert added.status_code == 201, added.text
    [item] = await _activate(client, scoped)
    response = await draft(client, scoped["lead"], item)
    refused = await _attach(client, scoped["lead"], response, {
        "external_location": "Test workpaper 4", "purpose": "independent_test",
        "tester_user_id": scoped["assignee"].id, "tested_at": "2026-03-15",
        "test_scope": "System access review", "conclusion": "No exceptions", "independence_basis": "Separate team",
    })
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.tester_not_independent"
