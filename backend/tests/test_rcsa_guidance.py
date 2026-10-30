"""IT 模板与人工信号：不预设评分、按期间解释矛盾、提交及复核分别守门。"""

import json
from datetime import date

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select

from app.rcsa.catalog import CATALOG, GUIDE_IDS
from app.rcsa.consistency import contradictions
from app.rcsa.models import RcsaCampaign
from app.rcsa.schemas import AdverseSignal, AnswerV1
from tests.rcsa_support import (
    COMPLETE_ANSWER,
    acme_world,
    activated,
    draft,
    get_json,
    rcsa_enabled,  # noqa: F401
    review,
    save,
    submit,
    submitted,
    transition,
)

pytestmark = pytest.mark.usefixtures("rcsa_enabled")
START, END = date(2026, 1, 1), date(2026, 3, 31)
SIGNAL = {
    "kind": "major_incident", "source_ref": "INC-EXAMPLE-1", "description": "Acme access service outage",
    "started_on": "2026-02-01", "ended_on": "2026-02-02", "explanation": "",
}


def test_catalog_links_are_valid_and_do_not_prescribe_scores_or_framework_mappings():
    assert len(CATALOG["risks"]) == len({r["id"] for r in CATALOG["risks"]}) == 31
    assert len(CATALOG["guides"]) == len(GUIDE_IDS) == 40
    for risk in CATALOG["risks"]:
        assert set(risk) == {"id", "domain", "title", "cause", "event", "impact", "guide_ids"}
        assert set(risk["guide_ids"]) <= GUIDE_IDS
        assert risk["cause"] and risk["event"] and risk["impact"]
    for guide in CATALOG["guides"]:
        assert guide["expected_evidence"] and guide["test_procedure"]


@pytest.mark.parametrize("kind", ["overdue_vulnerability", "major_incident", "failed_change", "red_kri", "open_finding"])
@pytest.mark.parametrize("start,end,expected", [
    ("2025-12-01", "2025-12-31", []),
    ("2025-12-01", "2026-01-01", [0]),
    ("2026-03-31", "2026-04-01", [0]),
    ("2026-04-01", None, []),
    ("2026-02-01", "2026-02-02", [0]),
    ("2025-12-01", None, [0]),
])
def test_signal_period_includes_resolved_conditions_during_the_campaign(kind, start, end, expected):
    answer = AnswerV1.model_validate(COMPLETE_ANSWER | {
        "operating": {"rating": "effective", "rationale": "Sample passed"},
        "signals": [SIGNAL | {"kind": kind, "started_on": start, "ended_on": end}],
    })
    assert contradictions(answer, START, END, has_risks=True) == expected


def test_effective_design_alone_does_not_claim_effective_operation():
    answer = AnswerV1.model_validate(COMPLETE_ANSWER | {"signals": [SIGNAL]})
    assert contradictions(answer, START, END, has_risks=True) == []
    assert contradictions(answer, START, END, has_risks=False) == [0]


@pytest.mark.parametrize("patch", [
    {"ended_on": "2026-01-01"}, {"source_ref": "  "}, {"kind": "unknown"}, {"description": ""},
])
def test_signal_rejects_invalid_or_untraceable_input(patch):
    with pytest.raises(ValidationError):
        AdverseSignal.model_validate(SIGNAL | patch)


@pytest.mark.parametrize("ids", [["unknown"], ["c-iam-01", "c-iam-01"]])
def test_guide_references_are_validated(ids):
    with pytest.raises(ValidationError):
        AnswerV1(guide_ids=ids)


@pytest_asyncio.fixture
async def acme(client, db_session):
    world = await acme_world(client, db_session)
    campaign, items = await activated(client, world, controls=1)
    return world | {"campaign": campaign, "item": items[0]}


async def test_catalog_requires_access_and_does_not_write_controls(client, acme):
    assert (await client.get("/api/rcsa/it-catalog")).status_code == 401
    result = await client.get("/api/rcsa/it-catalog", headers=acme["assignee"].headers)
    assert result.status_code == 200
    assert result.json() == CATALOG


async def test_signal_explanation_and_independent_review_are_required(client, acme):
    response = await draft(client, acme["assignee"], acme["item"])
    response = await save(client, acme["assignee"], response, COMPLETE_ANSWER | {
        "guide_ids": ["c-iam-01"], "signals": [SIGNAL],
        # 改作答期间不能绕过活动期间内的矛盾检查。
        "assessment_period_start": "2026-03-01",
    })
    assert response["contradiction_indices"] == [0]
    blocked = await submit(client, acme["assignee"], response)
    assert blocked.status_code == 409
    assert "rcsa.problem.signal_explanation" in blocked.json()["params"]["problems"]
    answer = response["answer"] | {"signals": [SIGNAL | {"explanation": "Scope checked against the incident record"}]}
    response = await save(client, acme["assignee"], response, answer)
    done = await submit(client, acme["assignee"], response)
    assert done.status_code == 200, done.text
    response = done.json()
    blocked = await review(client, acme["reviewer"], response, "approve")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "rcsa.signal_review_required"
    own = await review(client, acme["assignee"], response, "approve", reason="Self approval")
    assert own.status_code == 403
    accepted = await review(client, acme["reviewer"], response, "approve", reason="Verified scope and compensating evidence")
    assert accepted.status_code == 200, accepted.text
    immutable = await client.patch(f"/api/rcsa/responses/{response['id']}", json={
        "expected_version": accepted.json()["version"], "answer": COMPLETE_ANSWER,
    }, headers=acme["assignee"].headers)
    assert immutable.status_code == 409


async def test_return_preserves_signal_and_guide_history_and_requires_new_review(client, acme):
    response = await submitted(client, acme, acme["item"], COMPLETE_ANSWER | {
        "signals": [SIGNAL | {"explanation": "First explanation"}], "guide_ids": ["c-iam-02"],
    })
    assert (await review(client, acme["reviewer"], response, "return", reason="Recheck scope")).status_code == 200
    item = await get_json(client, acme, f"/api/rcsa/items/{acme['item']['id']}")
    revision = await draft(client, acme["assignee"], item)
    assert revision["answer"] == response["answer"]
    revision = await save(client, acme["assignee"], revision, revision["answer"] | {"signals": [], "guide_ids": []})
    history = await get_json(client, acme, f"/api/rcsa/items/{item['id']}/responses")
    assert history[0]["answer"]["signals"][0]["explanation"] == "First explanation"
    assert history[0]["answer"]["guide_ids"] == ["c-iam-02"]
    assert history[1]["answer"]["signals"] == []


async def test_closed_report_uses_frozen_signals_without_exposing_source_references(client, db_session, acme):
    response = await submitted(client, acme, acme["item"], COMPLETE_ANSWER | {
        "signals": [SIGNAL | {"explanation": "Checked the affected scope"}], "guide_ids": ["c-iam-01"],
    })
    assert (await review(client, acme["reviewer"], response, "approve", reason="Independent scope check")).status_code == 200
    assert (await transition(client, acme, "begin-review")).status_code == 200
    assert (await transition(client, acme, "close")).status_code == 200
    campaign = await db_session.scalar(select(RcsaCampaign).where(RcsaCampaign.id == acme["campaign"]["id"]))
    frozen = campaign.closed_snapshot["items"][0]["response"]["answer"]
    assert frozen["guide_ids"] == ["c-iam-01"]
    assert frozen["signals"][0]["source_ref"] == SIGNAL["source_ref"]
    report = await get_json(client, acme, f"/api/rcsa/campaigns/{campaign.id}/report")
    assert report["source"] == "closed_snapshot"
    assert report["signal_reviews"] == [{
        "item_id": acme["item"]["id"], "control_code": "C-9001", "target_label": None, "signals": 1,
        "contradictions": 1, "approval_rationale_recorded": True,
    }]
    assert SIGNAL["source_ref"] not in json.dumps(report)
