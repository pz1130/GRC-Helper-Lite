"""RCSA 的两个可选 AI 通道：默认关闭、分别授权、只发允许的输入、失败不碰人工草稿。

全部用假 provider：这里测的是关闭路径与守门逻辑，真实服务的效果另行验收（K20）。
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from app.crypto import encrypt
from app.decision.base import DecisionResponse
from app.evidence.models import EvidenceCadence, EvidenceItem, EvidenceStatus, EvidenceType
from app.iam.models import AuditLog
from app.iam.permissions import Role
from app.llm.models import (
    AppSetting,
    LLMCall,
    LLMProviderConfig,
    ProviderKind,
    RedactionRule,
    RulesetName,
)
from app.llm.providers.base import CompletionResponse, ProviderError
from app.rcsa.models import RcsaResponse
from app.review.models import Proposal
from tests.rcsa_support import (
    COMPLETE_ANSWER,
    acme_world,
    activated,
    draft,
    person,
    rcsa_enabled,  # noqa: F401 — fixture
    save,
    submit,
    with_key,
)

pytestmark = pytest.mark.usefixtures("rcsa_enabled")

SUGGESTION = {
    "design": {"rating": "effective", "rationale": "The policy requires a quarterly review", "citations": ["control"]},
    "operating": {"rating": "not_assessed", "rationale": "No operating evidence was provided", "citations": ["control"]},
    "limitations": "Only the control text was available",
    "unknowns": ["Whether reviews happened on time"],
}


def _reply(payload: dict) -> CompletionResponse:
    return CompletionResponse(text=json.dumps(payload), tokens_in=100, tokens_out=50)


@pytest_asyncio.fixture
async def acme(client, db_session):
    world = await acme_world(client, db_session)
    world["admin"] = await person(client, db_session, "admin", Role.ADMIN)
    _, items = await activated(client, world)
    chat = LLMProviderConfig(name="approved-chat", kind=ProviderKind.ANTHROPIC, model="claude-test",
                             api_key_encrypted=encrypt("sk-test"))
    backup = LLMProviderConfig(name="global-backup", kind=ProviderKind.ANTHROPIC, model="claude-backup",
                               api_key_encrypted=encrypt("sk-backup"), is_fallback=True)
    judge = LLMProviderConfig(name="judge", kind=ProviderKind.JEV, model="jev-test", api_key_encrypted=encrypt("k"))
    db_session.add_all([chat, backup, judge])
    await db_session.flush()
    response = await save(client, world["assignee"], await draft(client, world["assignee"], items[0]),
                          COMPLETE_ANSWER | {"design": {"rating": None, "rationale": ""}})
    return world | {"item": items[0], "response": response, "chat": chat, "backup": backup, "judge": judge}


async def _enable(client, acme, channel="prefill", **overrides):
    body = {
        "enabled": True, "approval_ref": "DPA-2026-07", "allowed_input_types": ["control"],
        "provider_config_id": acme["chat" if channel == "prefill" else "judge"].id,
    } | overrides
    return await client.put(f"/api/rcsa/ai/{channel}", json=body, headers=acme["admin"].headers)


async def _prefill(client, acme, key="p1"):
    return await client.post(f"/api/rcsa/responses/{acme['response']['id']}/prefill", json={},
                             headers=with_key(acme["assignee"], key))


async def _calls(db_session) -> int:
    return await db_session.scalar(select(func.count()).select_from(LLMCall))


# ── 默认关闭与授权 ──────────────────────────────────────────


async def test_both_channels_start_disabled_and_send_nothing(client, db_session, acme):
    status = (await client.get("/api/rcsa/ai", headers=acme["assignee"].headers)).json()
    assert (status["prefill"]["enabled"], status["evidence_relevance"]["enabled"]) == (False, False)

    with patch("app.llm.runner.build_provider", side_effect=AssertionError("network")):
        refused = await _prefill(client, acme)

    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.ai_disabled"
    assert await _calls(db_session) == 0


@pytest.mark.parametrize("overrides,code", [
    ({"approval_ref": ""}, "rcsa.ai_approval_required"),
    ({"allowed_input_types": []}, "rcsa.ai_inputs_required"),
])
async def test_enabling_needs_an_approval_reference_and_chosen_inputs(client, acme, overrides, code):
    refused = await _enable(client, acme, **overrides)
    assert refused.status_code == 409
    assert refused.json()["code"] == code


async def test_each_channel_takes_only_its_own_kind_of_service(client, acme):
    wrong = await _enable(client, acme, "prefill", provider_config_id=acme["judge"].id)
    assert wrong.json()["code"] == "rcsa.ai_provider_kind"
    wrong = await _enable(client, acme, "evidence_relevance", provider_config_id=acme["chat"].id)
    assert wrong.json()["code"] == "rcsa.ai_provider_kind"


async def test_only_admins_configure_the_channels(client, acme):
    refused = await client.put("/api/rcsa/ai/prefill", json={
        "enabled": True, "approval_ref": "x", "allowed_input_types": ["control"],
        "provider_config_id": acme["chat"].id,
    }, headers=acme["lead"].headers)
    assert refused.status_code == 403


async def test_the_document_scan_switch_does_not_open_evidence_ranking(client, db_session, acme):
    db_session.add(AppSetting(key="decision_model", value={"enabled": True}))
    await db_session.flush()
    refused = await client.post(f"/api/rcsa/responses/{acme['response']['id']}/rank-evidence", json={},
                                headers=acme["assignee"].headers)
    assert refused.json()["code"] == "rcsa.ai_disabled"


# ── 预填 ────────────────────────────────────────────────────


async def test_prefill_returns_a_suggestion_without_touching_the_draft(client, db_session, acme):
    await _enable(client, acme)
    with patch("app.llm.runner.build_provider", return_value=AsyncMock(complete=AsyncMock(return_value=_reply(SUGGESTION)))):
        made = await _prefill(client, acme)

    assert made.status_code == 200, made.text
    assert made.json()["stale"] is False
    assert made.json()["suggestions"]["design"]["rating"] == "effective"
    response = await db_session.get(RcsaResponse, acme["response"]["id"])
    await db_session.refresh(response)
    assert response.answer["design"]["rating"] is None
    call = await db_session.scalar(select(LLMCall))
    assert (call.task_key, call.status) == ("rcsa_prefill", "ok")


async def test_only_the_approved_input_types_leave_the_system(client, db_session, acme):
    evidence_type = EvidenceType(name_zh="t", name_en="Review log", cadence=EvidenceCadence.QUARTERLY)
    db_session.add(evidence_type)
    await db_session.flush()
    record = EvidenceItem(evidence_type_id=evidence_type.id, control_id=acme["item"]["control_id"],
                          title="Secret export title", status=EvidenceStatus.PLANNED)
    db_session.add(record)
    await db_session.flush()
    await client.post(f"/api/rcsa/responses/{acme['response']['id']}/evidence",
                      json={"evidence_item_id": record.id, "purpose": "self_assessment"}, headers=acme["assignee"].headers)
    acme["response"] = (await client.get(f"/api/rcsa/responses/{acme['response']['id']}", headers=acme["assignee"].headers)).json()
    await _enable(client, acme, allowed_input_types=["control"])
    complete = AsyncMock(return_value=_reply(SUGGESTION))

    with patch("app.llm.runner.build_provider", return_value=AsyncMock(complete=complete)):
        await _prefill(client, acme)

    sent = complete.call_args.args[0].prompt
    assert "Acme does thing 1." in sent
    assert "Secret export title" not in sent


async def test_redaction_rules_apply_before_sending(client, db_session, acme):
    db_session.add(RedactionRule(ruleset=RulesetName.GENERATION, pattern_type="regex",
                                 pattern=r"Acme does thing \d", replacement_prefix="ORG", order_index=1))
    await db_session.flush()
    await _enable(client, acme)
    complete = AsyncMock(return_value=_reply(SUGGESTION))

    with patch("app.llm.runner.build_provider", return_value=AsyncMock(complete=complete)):
        await _prefill(client, acme)

    sent = complete.call_args.args[0].prompt
    assert "Acme does thing" not in sent and "ORG_1" in sent


async def test_policy_text_alone_cannot_make_operation_effective(client, db_session, acme):
    await _enable(client, acme)
    claimed = SUGGESTION | {"operating": {"rating": "effective", "rationale": "Policy says so", "citations": ["control"]}}
    with patch("app.llm.runner.build_provider", return_value=AsyncMock(complete=AsyncMock(return_value=_reply(claimed)))):
        refused = await _prefill(client, acme)

    assert refused.status_code == 502
    assert refused.json()["code"] == "rcsa.prefill_unavailable"
    call = await db_session.scalar(select(LLMCall))
    assert call.status == "error"


async def test_a_failing_service_keeps_the_draft_and_never_uses_the_global_backup(client, db_session, acme):
    await _enable(client, acme)
    built = []

    def build(config):
        built.append(config.name)
        return AsyncMock(complete=AsyncMock(side_effect=ProviderError("429", retryable=True)))

    with patch("app.llm.runner.build_provider", side_effect=build), patch("app.llm.runner._sleep", new=AsyncMock()):
        refused = await _prefill(client, acme)

    assert refused.json()["code"] == "rcsa.prefill_unavailable"
    assert built == ["approved-chat"]
    assert await db_session.scalar(select(func.count()).select_from(Proposal)) == 0


async def test_switching_off_during_the_call_discards_the_result(client, db_session, acme):
    await _enable(client, acme)

    async def complete(request):
        await db_session.execute(update(AppSetting).where(AppSetting.key == "rcsa_ai.prefill").values(
            value={"enabled": False, "approval_ref": "DPA-2026-07", "allowed_input_types": ["control"]}))
        return _reply(SUGGESTION)

    with patch("app.llm.runner.build_provider", return_value=AsyncMock(complete=complete)):
        refused = await _prefill(client, acme)

    assert refused.json()["code"] == "rcsa.ai_disabled"
    assert await db_session.scalar(select(func.count()).select_from(Proposal)) == 0
    assert await _calls(db_session) == 1


async def test_a_draft_edited_during_the_call_gets_a_stale_suggestion(client, db_session, acme):
    await _enable(client, acme)

    async def complete(request):
        await db_session.execute(update(RcsaResponse).where(RcsaResponse.id == acme["response"]["id"]).values(
            version=RcsaResponse.version + 1))
        return _reply(SUGGESTION)

    with patch("app.llm.runner.build_provider", return_value=AsyncMock(complete=complete)):
        made = (await _prefill(client, acme)).json()

    assert made["stale"] is True
    response = (await client.get(f"/api/rcsa/responses/{acme['response']['id']}", headers=acme["assignee"].headers)).json()
    refused = await client.post(f"/api/rcsa/suggestions/{made['id']}/apply",
                                json={"fields": ["design"], "expected_version": response["version"]},
                                headers=acme["assignee"].headers)
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.suggestion_stale"


async def test_the_same_request_key_does_not_call_the_model_twice(client, db_session, acme):
    await _enable(client, acme)
    complete = AsyncMock(return_value=_reply(SUGGESTION))
    with patch("app.llm.runner.build_provider", return_value=AsyncMock(complete=complete)):
        first = (await _prefill(client, acme, key="same")).json()
        again = (await _prefill(client, acme, key="same")).json()

    assert again["id"] == first["id"]
    assert complete.call_count == 1


async def test_only_the_assessor_asks_for_a_suggestion(client, acme):
    await _enable(client, acme)
    refused = await client.post(f"/api/rcsa/responses/{acme['response']['id']}/prefill", json={},
                                headers=with_key(acme["reviewer"], "x"))
    assert refused.status_code == 403


# ── 应用 ────────────────────────────────────────────────────


async def _suggestion(client, acme) -> dict:
    await _enable(client, acme)
    with patch("app.llm.runner.build_provider", return_value=AsyncMock(complete=AsyncMock(return_value=_reply(SUGGESTION)))):
        return (await _prefill(client, acme)).json()


async def test_applying_copies_only_the_chosen_fields_and_records_where_they_came_from(client, db_session, acme):
    made = await _suggestion(client, acme)

    applied = await client.post(f"/api/rcsa/suggestions/{made['id']}/apply",
                                json={"fields": ["design"], "expected_version": acme["response"]["version"]},
                                headers=acme["assignee"].headers)

    assert applied.status_code == 200, applied.text
    answer = applied.json()["answer"]
    assert answer["design"] == {"rating": "effective", "rationale": "The policy requires a quarterly review"}
    assert answer["operating"] == COMPLETE_ANSWER["operating"]
    audit = await db_session.scalar(select(AuditLog).where(AuditLog.action == "rcsa.ai_apply"))
    assert audit.after == {"proposal_id": made["id"], "fields": ["design"], "response_id": acme["response"]["id"]}
    again = await client.post(f"/api/rcsa/suggestions/{made['id']}/apply",
                              json={"fields": ["operating"], "expected_version": applied.json()["version"]},
                              headers=acme["assignee"].headers)
    assert again.json()["code"] == "rcsa.suggestion_used"


async def test_a_suggestion_cannot_reach_a_submitted_answer(client, acme):
    made = await _suggestion(client, acme)
    response = (await client.get(f"/api/rcsa/responses/{acme['response']['id']}", headers=acme["assignee"].headers)).json()
    submitted = await submit(client, acme["assignee"], await save(client, acme["assignee"], response, COMPLETE_ANSWER))
    assert submitted.status_code == 200, submitted.text

    refused = await client.post(f"/api/rcsa/suggestions/{made['id']}/apply",
                                json={"fields": ["design"], "expected_version": submitted.json()["version"]},
                                headers=acme["assignee"].headers)
    assert refused.status_code == 409
    assert refused.json()["code"] == "rcsa.response_not_draft"


async def test_rcsa_suggestions_stay_out_of_the_review_queue(client, acme):
    made = await _suggestion(client, acme)

    queue = (await client.get("/api/proposals", headers=acme["lead"].headers)).json()
    stats = (await client.get("/api/proposals/stats", headers=acme["lead"].headers)).json()
    decided = await client.post(f"/api/proposals/{made['id']}/decide", json={"decision": "reject", "reason": "x"},
                                headers=acme["lead"].headers)

    assert made["id"] not in [p["id"] for p in queue]
    assert stats["pending"] == 0
    assert decided.status_code == 409
    assert decided.json()["code"] == "review.kind_not_in_queue"


# ── 证据相关性 ──────────────────────────────────────────────


async def _evidence(db_session, acme, titles):
    evidence_type = EvidenceType(name_zh="t", name_en="Log", cadence=EvidenceCadence.QUARTERLY)
    db_session.add(evidence_type)
    await db_session.flush()
    rows = [EvidenceItem(evidence_type_id=evidence_type.id, control_id=acme["item"]["control_id"], title=title,
                         status=EvidenceStatus.PLANNED) for title in titles]
    db_session.add_all(rows)
    await db_session.flush()
    return rows


async def test_ranking_orders_candidates_by_the_judges_probability(client, db_session, acme, monkeypatch):
    weak, strong = await _evidence(db_session, acme, ["Old screenshot", "Q1 review export"])
    await _enable(client, acme, "evidence_relevance", allowed_input_types=["control", "evidence_metadata"])
    fake = AsyncMock(decide=AsyncMock(return_value=DecisionResponse(
        probabilities={f"e{weak.id}": 0.2, f"e{strong.id}": 0.9}, model="jev-test", tokens_in=10, tokens_out=0)))
    monkeypatch.setattr("app.rcsa.ai.build_decision_provider", lambda config: fake)

    ranked = (await client.post(f"/api/rcsa/responses/{acme['response']['id']}/rank-evidence", json={},
                                headers=acme["assignee"].headers)).json()

    assert ranked["ranked"] is True
    assert [c["title"] for c in ranked["candidates"]] == ["Q1 review export", "Old screenshot"]


async def test_a_failed_ranking_still_lists_every_candidate(client, db_session, acme, monkeypatch):
    await _evidence(db_session, acme, ["A", "B"])
    await _enable(client, acme, "evidence_relevance", allowed_input_types=["control", "evidence_metadata"])
    fake = AsyncMock(decide=AsyncMock(side_effect=ProviderError("timeout", retryable=True)))
    monkeypatch.setattr("app.rcsa.ai.build_decision_provider", lambda config: fake)

    ranked = (await client.post(f"/api/rcsa/responses/{acme['response']['id']}/rank-evidence", json={},
                                headers=acme["assignee"].headers)).json()

    assert (ranked["ranked"], ranked["reason"]) == (False, "rcsa.ranking_unavailable")
    assert sorted(c["title"] for c in ranked["candidates"]) == ["A", "B"]
    assert all(c["probability"] is None for c in ranked["candidates"])
