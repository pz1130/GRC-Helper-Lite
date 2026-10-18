"""RCSA 2B：作答草稿、证据引用、提交冻结、复核（批准/退回）与排除。"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.evidence.models import EvidenceCadence, EvidenceItem, EvidenceStatus, EvidenceType
from app.iam.models import AuditLog
from app.iam.permissions import Role
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
    submitted,
    with_key,
)

pytestmark = pytest.mark.usefixtures("rcsa_enabled")


@pytest_asyncio.fixture
async def acme(client, db_session):
    world = await acme_world(client, db_session)
    campaign, items = await activated(client, world)
    return world | {"campaign": campaign, "item": items[0]}


async def _item(client, acme) -> dict:
    return (await client.get(f"/api/rcsa/items/{acme['item']['id']}", headers=acme["lead"].headers)).json()


async def _evidence_item(db_session, acme, **overrides) -> EvidenceItem:
    evidence_type = EvidenceType(name_zh="Log", name_en="Review log", cadence=EvidenceCadence.QUARTERLY)
    db_session.add(evidence_type)
    await db_session.flush()
    fields = {
        "evidence_type_id": evidence_type.id, "control_id": acme["lib"].controls[0].id,
        "title": "Q1 access review export", "status": EvidenceStatus.COLLECTED,
        "last_collected_at": datetime(2026, 2, 1, tzinfo=UTC),
        "valid_until": datetime(2099, 1, 1, tzinfo=UTC),
    }
    evidence = EvidenceItem(**(fields | overrides))
    db_session.add(evidence)
    await db_session.flush()
    return evidence


async def _attach(client, who, response, body):
    return await client.post(f"/api/rcsa/responses/{response['id']}/evidence", json=body, headers=who.headers)


# ── 草稿 ────────────────────────────────────────────────────


async def test_assignee_opens_the_first_revision_as_a_draft(client, acme):
    response = await draft(client, acme["assignee"], acme["item"])

    assert (response["revision"], response["status"]) == (1, "draft")
    item = await _item(client, acme)
    assert (item["status"], item["current_response_id"]) == ("draft", response["id"])


@pytest.mark.parametrize("who", ["reviewer", "lead"])
async def test_only_the_assignee_can_answer(client, acme, who):
    refused = await client.post(
        f"/api/rcsa/items/{acme['item']['id']}/responses",
        json={"expected_version": acme["item"]["version"]}, headers=acme[who].headers,
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "rcsa.not_assignee"


async def test_viewer_cannot_answer(client, acme):
    refused = await client.post(
        f"/api/rcsa/items/{acme['item']['id']}/responses",
        json={"expected_version": acme["item"]["version"]}, headers=acme["viewer"].headers,
    )
    assert refused.status_code == 403


async def test_a_second_draft_is_refused(client, acme):
    await draft(client, acme["assignee"], acme["item"])
    again = await client.post(
        f"/api/rcsa/items/{acme['item']['id']}/responses",
        json={"expected_version": (await _item(client, acme))["version"]},
        headers=acme["assignee"].headers,
    )
    assert again.status_code == 409
    assert again.json()["code"] == "rcsa.draft_exists"


async def test_someone_elses_draft_cannot_be_edited(client, acme):
    response = await draft(client, acme["assignee"], acme["item"])
    refused = await client.patch(
        f"/api/rcsa/responses/{response['id']}",
        json={"answer": COMPLETE_ANSWER, "expected_version": response["version"]},
        headers=acme["reviewer"].headers,
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "rcsa.not_assignee"


async def test_saving_over_a_newer_version_is_a_conflict(client, acme):
    response = await draft(client, acme["assignee"], acme["item"])
    await save(client, acme["assignee"], response, COMPLETE_ANSWER)
    stale = await client.patch(
        f"/api/rcsa/responses/{response['id']}",
        json={"answer": COMPLETE_ANSWER, "expected_version": response["version"]},
        headers=acme["assignee"].headers,
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "rcsa.version_conflict"


async def test_answer_with_unknown_fields_is_rejected(client, acme):
    response = await draft(client, acme["assignee"], acme["item"])
    refused = await client.patch(
        f"/api/rcsa/responses/{response['id']}",
        json={"answer": COMPLETE_ANSWER | {"score": 99}, "expected_version": response["version"]},
        headers=acme["assignee"].headers,
    )
    assert refused.status_code == 422


async def test_deactivated_assignee_cannot_keep_working(client, db_session, acme):
    response = await draft(client, acme["assignee"], acme["item"])
    acme["assignee"].user.is_active = False
    await db_session.flush()
    refused = await client.patch(
        f"/api/rcsa/responses/{response['id']}",
        json={"answer": COMPLETE_ANSWER, "expected_version": response["version"]},
        headers=acme["assignee"].headers,
    )
    assert refused.status_code == 401


# ── 提交 ────────────────────────────────────────────────────


async def _problems(client, acme, answer) -> set[str]:
    response = await save(client, acme["assignee"], await draft(client, acme["assignee"], acme["item"]), answer)
    refused = await submit(client, acme["assignee"], response)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "rcsa.submission_incomplete"
    return set(refused.json()["params"]["problems"])


async def test_empty_answer_cannot_be_submitted(client, acme):
    problems = await _problems(client, acme, {})
    assert {
        "rcsa.problem.design_rating", "rcsa.problem.operating_rating",
        "rcsa.problem.assessment_period", "rcsa.problem.independent_test_status",
        "rcsa.problem.risks_or_rationale",
    } <= problems


async def test_not_assessed_needs_a_reason_and_a_follow_up(client, acme):
    answer = COMPLETE_ANSWER | {"operating": {"rating": "not_assessed", "rationale": "No logs retained"}}
    assert {"rcsa.problem.not_assessed_reason", "rcsa.problem.evidence_followup"} <= await _problems(
        client, acme, answer
    )


async def test_not_assessed_with_reason_and_follow_up_submits(client, acme):
    answer = COMPLETE_ANSWER | {
        "operating": {"rating": "not_assessed", "rationale": "No logs retained"},
        "not_assessed_reason": "Log retention was 30 days, the period is 90",
        "evidence_followup": {
            "owner_id": acme["assignee"].id, "due_at": "2026-06-30",
            "description": "Extend retention and re-collect",
        },
    }
    done = await submitted(client, acme, acme["item"], answer)
    assert done["status"] == "submitted"


async def test_claimed_independent_test_needs_independent_test_evidence(client, acme):
    answer = COMPLETE_ANSWER | {"independent_test_status": "provided", "independent_test_reason": ""}
    assert "rcsa.problem.independent_test_evidence" in await _problems(client, acme, answer)


async def test_missing_independent_test_needs_a_reason(client, acme):
    answer = COMPLETE_ANSWER | {"independent_test_reason": ""}
    assert "rcsa.problem.independent_test_reason" in await _problems(client, acme, answer)


async def test_submission_freezes_the_answer(client, acme):
    response = await submitted(client, acme, acme["item"])
    item = await _item(client, acme)
    assert item["status"] == "submitted"
    refused = await client.patch(
        f"/api/rcsa/responses/{response['id']}",
        json={"answer": COMPLETE_ANSWER, "expected_version": response["version"]},
        headers=acme["assignee"].headers,
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.response_not_draft"


async def test_repeated_submit_with_the_same_key_replays(client, db_session, acme):
    response = await save(client, acme["assignee"], await draft(client, acme["assignee"], acme["item"]),
                          COMPLETE_ANSWER)
    first = await submit(client, acme["assignee"], response, key="same")
    again = await submit(client, acme["assignee"], response, key="same")
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert await db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "rcsa.submit")
    ) == 1


# ── 证据 ────────────────────────────────────────────────────


async def test_registered_evidence_is_snapshotted_and_frozen_at_submission(client, db_session, acme):
    evidence = await _evidence_item(db_session, acme)
    response = await save(client, acme["assignee"], await draft(client, acme["assignee"], acme["item"]),
                          COMPLETE_ANSWER)
    attached = await _attach(client, acme["assignee"], response,
                             {"evidence_item_id": evidence.id, "purpose": "self_assessment"})
    assert attached.status_code == 201, attached.text
    response = (await client.get(f"/api/rcsa/responses/{response['id']}", headers=acme["assignee"].headers)).json()
    assert (await submit(client, acme["assignee"], response)).status_code == 200

    evidence.title = "Renamed later"
    await db_session.flush()

    frozen = (await client.get(f"/api/rcsa/responses/{response['id']}", headers=acme["lead"].headers)).json()
    assert frozen["evidence"][0]["snapshot"]["title"] == "Q1 access review export"
    refused = await _attach(client, acme["assignee"], frozen, {"external_location": "Ticket ACME-9",
                                                              "purpose": "self_assessment"})
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.response_not_draft"


async def test_links_carrying_credentials_are_refused(client, acme):
    response = await draft(client, acme["assignee"], acme["item"])
    for location in (
        "https://user:secret@files.acme.example/review.xlsx",
        "https://files.acme.example/review.xlsx?sig=abc123",
    ):
        refused = await _attach(client, acme["assignee"], response,
                                {"external_location": location, "purpose": "self_assessment"})
        assert refused.status_code == 400, location
        assert refused.json()["code"] == "rcsa.evidence_location_has_credentials"


async def test_assessor_cannot_be_their_own_independent_tester(client, acme):
    response = await draft(client, acme["assignee"], acme["item"])
    refused = await _attach(client, acme["assignee"], response, {
        "external_location": "Test workpaper 4", "purpose": "independent_test",
        "tester_user_id": acme["assignee"].id, "tested_at": "2026-03-15",
        "test_scope": "All Q1 reviews", "conclusion": "No exceptions",
        "independence_basis": "Separate team",
    })
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.tester_not_independent"


async def test_independent_test_needs_its_structured_fields(client, acme):
    response = await draft(client, acme["assignee"], acme["item"])
    refused = await _attach(client, acme["assignee"], response, {
        "external_location": "Test workpaper 4", "purpose": "independent_test",
        "tester_name": "Internal audit",
    })
    assert refused.status_code == 422


async def test_evidence_is_removed_only_through_its_own_response(client, acme):
    response = await draft(client, acme["assignee"], acme["item"])
    attached = (await _attach(client, acme["assignee"], response,
                              {"external_location": "Ticket ACME-1", "purpose": "self_assessment"})).json()
    wrong = await client.delete(
        f"/api/rcsa/responses/{response['id'] + 999}/evidence/{attached['id']}", headers=acme["assignee"].headers
    )
    assert wrong.status_code == 404
    removed = await client.delete(
        f"/api/rcsa/responses/{response['id']}/evidence/{attached['id']}", headers=acme["assignee"].headers
    )
    assert removed.status_code == 204


async def test_expired_evidence_needs_an_acknowledged_limitation(client, db_session, acme):
    evidence = await _evidence_item(db_session, acme, valid_until=datetime(2026, 1, 31, tzinfo=UTC))
    response = await save(client, acme["assignee"], await draft(client, acme["assignee"], acme["item"]),
                          COMPLETE_ANSWER)
    await _attach(client, acme["assignee"], response, {"evidence_item_id": evidence.id, "purpose": "self_assessment"})
    response = (await client.get(f"/api/rcsa/responses/{response['id']}", headers=acme["assignee"].headers)).json()

    refused = await submit(client, acme["assignee"], response)
    assert "rcsa.problem.evidence_limitations" in refused.json()["params"]["problems"]

    response = await save(client, acme["assignee"], response, COMPLETE_ANSWER | {
        "evidence_limitations_acknowledged": True, "limitations": "Export expired before period end",
    })
    assert (await submit(client, acme["assignee"], response, key="submit-2")).status_code == 200


# ── 复核 ────────────────────────────────────────────────────


async def test_reviewer_approves_the_submitted_revision(client, db_session, acme):
    response = await submitted(client, acme, acme["item"])
    approved = await review(client, acme["reviewer"], response, "approve")

    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert (await _item(client, acme))["status"] == "approved"
    reviews = (await client.get(f"/api/rcsa/items/{acme['item']['id']}/reviews", headers=acme["viewer"].headers)).json()
    assert [(r["decision"], r["target_version"]) for r in reviews] == [("approve", 1)]


async def test_assignee_cannot_review_their_own_answer(client, acme):
    response = await submitted(client, acme, acme["item"])
    refused = await review(client, acme["assignee"], response, "approve")
    assert refused.status_code == 403


async def test_admin_cannot_approve_what_they_submitted(client, db_session, acme):
    admin = await person(client, db_session, "admin", Role.ADMIN)
    item = await _item(client, acme)
    moved = await client.post(f"/api/rcsa/items/{item['id']}/assign",
                              json={"assignee_id": admin.id, "reason": "Cover", "expected_version": item["version"]},
                              headers=acme["lead"].headers)
    assert moved.status_code == 200, moved.text
    response = await save(client, admin, await draft(client, admin, moved.json()), COMPLETE_ANSWER)
    assert (await submit(client, admin, response)).status_code == 200
    item = await _item(client, acme)
    swapped = await client.post(f"/api/rcsa/items/{item['id']}/assign",
                                json={"assignee_id": acme["assignee"].id, "reviewer_id": admin.id,
                                      "reason": "Swap", "expected_version": item["version"]},
                                headers=acme["lead"].headers)
    assert swapped.status_code == 200, swapped.text
    response = (await client.get(f"/api/rcsa/responses/{response['id']}", headers=admin.headers)).json()

    refused = await review(client, admin, response, "approve")

    assert refused.status_code == 403
    assert refused.json()["code"] == "rcsa.reviewer_is_submitter"


async def test_return_needs_a_reason(client, acme):
    response = await submitted(client, acme, acme["item"])
    refused = await review(client, acme["reviewer"], response, "return", reason="")
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.reason_required"


async def test_returned_answer_is_kept_and_copied_into_a_new_revision(client, acme):
    first = await submitted(client, acme, acme["item"])
    returned = await review(client, acme["reviewer"], first, "return", reason="Rationale too thin")
    assert returned.json()["status"] == "returned"
    assert (await _item(client, acme))["status"] == "returned"

    second = await draft(client, acme["assignee"], await _item(client, acme))

    assert second["revision"] == 2
    assert second["answer"] == first["answer"]
    history = (await client.get(f"/api/rcsa/items/{acme['item']['id']}/responses", headers=acme["lead"].headers)).json()
    assert [(r["revision"], r["status"]) for r in history] == [(1, "returned"), (2, "draft")]


async def test_reviewing_a_superseded_revision_is_a_conflict(client, acme):
    first = await submitted(client, acme, acme["item"])
    await review(client, acme["reviewer"], first, "return", reason="Rationale too thin")
    first = (await client.get(f"/api/rcsa/responses/{first['id']}", headers=acme["lead"].headers)).json()
    await submitted(client, acme, await _item(client, acme))

    stale = await review(client, acme["reviewer"], first, "approve", key="late")

    assert stale.status_code == 409
    assert stale.json()["code"] == "rcsa.stale_revision"


async def test_reviewer_excludes_an_item_with_a_reason(client, acme):
    item = await _item(client, acme)
    excluded = await client.post(
        f"/api/rcsa/items/{item['id']}/exclude",
        json={"reason": "System decommissioned in January", "expected_version": item["version"]},
        headers=with_key(acme["reviewer"], "exclude-1"),
    )
    assert excluded.status_code == 200, excluded.text
    assert excluded.json()["status"] == "excluded"
    refused = await client.post(
        f"/api/rcsa/items/{item['id']}/responses",
        json={"expected_version": excluded.json()["version"]}, headers=acme["assignee"].headers,
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.item_state"


async def test_only_the_assigned_reviewer_excludes(client, acme):
    item = await _item(client, acme)
    refused = await client.post(
        f"/api/rcsa/items/{item['id']}/exclude",
        json={"reason": "Not needed", "expected_version": item["version"]},
        headers=with_key(acme["lead"], "exclude-1"),
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "rcsa.not_reviewer"
