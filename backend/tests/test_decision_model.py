"""仅用合成条款和假服务，验证可选判断角色不会绕过外发开关。"""

import json

import httpx
import pytest
from sqlalchemy import select

from app.clauses.models import Clause
from app.crypto import encrypt
from app.decision.base import DecisionRequest, DecisionResponse
from app.decision.jev import JevProvider
from app.decision.service import read_scan, run_scan
from app.decision.settings import DecisionSettings, save_settings
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.ingest.models import DocStatus, DocType, Document
from app.llm.models import (
    AppSetting,
    LLMCall,
    LLMProviderConfig,
    ProviderKind,
    RedactionRule,
    RulesetName,
)
from app.llm.providers.base import ProviderError

RULE = "Operators must review all access permissions monthly and record the results."
IMPLICIT = "The service team reviews access records every week and keeps an approval log."


async def setup_scan(session, texts=None):
    user = User(email="scan@example.com", name="Reviewer", role=Role.ADMIN, password_hash=hash_password("pw123456"))
    doc = Document(title="Example policy", doc_type=DocType.POLICY, status=DocStatus.ACTIVE,
                   file_hash="d" * 64, file_path="/synthetic", original_filename="example.txt")
    config = LLMProviderConfig(name="Decision", kind=ProviderKind.JEV, model="jev-1.13.0",
                               api_key_encrypted=encrypt("synthetic-key"), enabled=True)
    session.add_all([user, doc, config])
    await session.flush()
    clauses = []
    for i, content in enumerate(texts or [RULE, IMPLICIT]):
        clause = Clause(document_id=doc.id, text=content, heading="Example", heading_path="Example",
                        citation_label=str(i + 1), order_index=i, level=1)
        clauses.append(clause)
    session.add_all(clauses)
    await session.flush()
    return user, doc, config, clauses


class FakeDecision:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def decide(self, req):
        self.calls.append(req)
        if self.fail:
            raise ProviderError("upstream echoed confidential content", retryable=True)
        return DecisionResponse({key: 0.9 for key in req.questions}, "jev-1.13.0", 100, 10)


def install(monkeypatch, fake):
    monkeypatch.setattr("app.decision.service.build_decision_provider", lambda _: fake)


async def enable(session, config):
    await save_settings(session, DecisionSettings(enabled=True, provider_config_id=config.id))


async def test_disabled_never_calls_and_read_never_calls(db_session, monkeypatch):
    user, doc, config, _ = await setup_scan(db_session)
    fake = FakeDecision()
    install(monkeypatch, fake)
    result = await run_scan(db_session, doc.id, user)
    assert result.reasons == ["disabled"] and result.mode == "rules"
    assert result.requirements == 1
    assert fake.calls == []
    await enable(db_session, config)
    result = await read_scan(db_session, doc.id)
    assert result.reasons == ["not_run"] and fake.calls == []


async def test_missing_and_disabled_providers_fall_back(db_session, monkeypatch):
    user, doc, config, _ = await setup_scan(db_session)
    fake = FakeDecision()
    install(monkeypatch, fake)
    db_session.add(AppSetting(key="decision_model", value={"enabled": True}))
    await db_session.flush()
    assert (await run_scan(db_session, doc.id, user)).reasons == ["not_configured"]
    await enable(db_session, config)
    config.enabled = False
    await db_session.flush()
    assert (await run_scan(db_session, doc.id, user)).reasons == ["provider_unavailable"]
    assert fake.calls == []


async def test_success_redacts_logs_and_reuses_cache(db_session, monkeypatch):
    user, doc, config, _ = await setup_scan(db_session, [IMPLICIT + " Contact analyst@example.com."])
    db_session.add(RedactionRule(ruleset=RulesetName.GENERATION, pattern_type="dictionary",
                                pattern="analyst@example.com", replacement_prefix="EMAIL", enabled=True, order_index=1))
    await enable(db_session, config)
    fake = FakeDecision()
    install(monkeypatch, fake)
    result = await run_scan(db_session, doc.id, user)
    assert result.mode == "model_assisted" and result.requirements == 1
    assert result.rule_fallback_count == 0 and result.reasons == []
    assert "analyst@example.com" not in json.dumps(fake.calls[0].questions)
    assert "[[EMAIL_" in json.dumps(fake.calls[0].questions)
    await run_scan(db_session, doc.id, user)
    assert len(fake.calls) == 1
    log = await db_session.scalar(select(LLMCall).where(LLMCall.task_key == "requirement_decision"))
    assert log.tokens_in == 100 and log.redaction_applied and log.cost_unknown
    assert log.error is None


async def test_failure_is_visible_and_never_logs_upstream_text(db_session, monkeypatch):
    user, doc, config, _ = await setup_scan(db_session)
    await enable(db_session, config)
    fake = FakeDecision(fail=True)
    install(monkeypatch, fake)
    result = await run_scan(db_session, doc.id, user)
    assert result.mode == "rules" and result.reasons == ["call_failed"]
    assert result.requirements == 1 and result.rule_fallback_count == 2
    log = await db_session.scalar(select(LLMCall).where(LLMCall.task_key == "requirement_decision"))
    assert log.error == "call_failed"
    assert (await read_scan(db_session, doc.id)).reasons == ["call_failed"]
    fake.fail = False
    assert (await run_scan(db_session, doc.id, user)).model_count == 2


async def test_model_cannot_remove_keyword_hits(db_session, monkeypatch):
    user, doc, config, clauses = await setup_scan(db_session, [RULE])
    await enable(db_session, config)
    class Negative(FakeDecision):
        async def decide(self, req):
            return DecisionResponse({str(clauses[0].id): 0.01}, "jev-1.13.0", 1, 1)
    install(monkeypatch, Negative())
    result = await run_scan(db_session, doc.id, user)
    assert result.requirements == 1 and result.rows[0].rule_match
    assert result.rows[0].probability == 0.01


async def test_cache_invalidates_on_text_model_and_redaction_changes(db_session, monkeypatch):
    user, doc, config, clauses = await setup_scan(db_session)
    await enable(db_session, config)
    fake = FakeDecision()
    install(monkeypatch, fake)
    await run_scan(db_session, doc.id, user)
    clauses[0].text += " Updated requirement."
    await db_session.flush()
    assert (await read_scan(db_session, doc.id)).reasons == ["stale"]
    await run_scan(db_session, doc.id, user)
    config.model = "jev-preview"
    await db_session.flush()
    assert (await read_scan(db_session, doc.id)).reasons == ["stale"]
    await run_scan(db_session, doc.id, user)
    db_session.add(RedactionRule(ruleset=RulesetName.GENERATION, pattern_type="dictionary",
                                pattern="Example", replacement_prefix="ORG", enabled=True, order_index=1))
    await db_session.flush()
    assert (await read_scan(db_session, doc.id)).reasons == ["stale"]
    await save_settings(db_session, DecisionSettings(enabled=False, provider_config_id=config.id))
    assert (await read_scan(db_session, doc.id)).mode == "rules"


async def test_oversized_clause_is_not_sent_or_truncated(db_session, monkeypatch):
    user, doc, config, clauses = await setup_scan(db_session, [RULE * 200, IMPLICIT])
    await enable(db_session, config)
    fake = FakeDecision()
    install(monkeypatch, fake)
    result = await run_scan(db_session, doc.id, user)
    assert result.reasons == ["input_too_large"] and result.rule_fallback_count == 1
    assert list(fake.calls[0].questions) == [str(clauses[1].id)]


async def test_exhausted_budget_prevents_sending(db_session, monkeypatch):
    user, doc, config, _ = await setup_scan(db_session)
    await enable(db_session, config)
    fake = FakeDecision()
    install(monkeypatch, fake)
    from app.llm.budget import BudgetExceeded
    async def exhausted(*args, **kwargs):
        raise BudgetExceeded("test budget", code="budget.exhausted")
    monkeypatch.setattr("app.decision.service.budget.check", exhausted)
    assert (await run_scan(db_session, doc.id, user)).reasons == ["budget_exhausted"]
    assert fake.calls == []


async def test_partial_retry_only_sends_unassessed_clauses(db_session, monkeypatch):
    user, doc, config, _ = await setup_scan(db_session)
    await enable(db_session, config)
    monkeypatch.setattr("app.decision.service.MAX_BATCH_ITEMS", 1)
    class Partial(FakeDecision):
        async def decide(self, req):
            if len(self.calls) == 1 and not self.fail:
                self.calls.append(req)
                raise ProviderError("failed", retryable=True)
            return await super().decide(req)
    fake = Partial()
    install(monkeypatch, fake)
    result = await run_scan(db_session, doc.id, user)
    assert result.model_count == 1 and result.rule_fallback_count == 1
    result = await run_scan(db_session, doc.id, user)
    assert result.model_count == 2 and result.reasons == []
    assert len(fake.calls) == 3


async def test_settings_and_scan_permissions_and_role_isolation(client, db_session, monkeypatch):
    user, doc, config, _ = await setup_scan(db_session)
    async def headers():
        r = await client.post("/api/auth/login", json={"email": user.email, "password": "pw123456"})
        return {"Authorization": "Bearer " + r.json()["access_token"]}
    auth = await headers()
    r = await client.get("/api/settings/decision-model", headers=auth)
    assert r.json()["enabled"] is False and "jev" in r.json()["provider_kinds"]
    r = await client.put("/api/settings/decision-model", json={"enabled": True, "provider_config_id": config.id}, headers=auth)
    assert r.status_code == 200
    r = await client.put("/api/settings/routing/bulk", json={"provider_config_id": config.id, "capability": "chat"}, headers=auth)
    assert r.status_code == 400
    r = await client.put("/api/settings/routing", json={"provider_config_id": config.id, "task_key": "control_extract"}, headers=auth)
    assert r.status_code == 400
    assert (await client.delete(f"/api/settings/providers/{config.id}", headers=auth)).status_code == 409
    fake = FakeDecision()
    install(monkeypatch, fake)
    user.role = Role.VIEWER
    await db_session.flush()
    assert (await client.get(f"/api/documents/{doc.id}/requirement-scan", headers=auth)).status_code == 200
    assert (await client.post(f"/api/documents/{doc.id}/requirement-scan", headers=auth)).status_code == 403
    assert (await client.get("/api/settings/decision-model", headers=auth)).status_code == 403
    assert fake.calls == []


async def test_jev_native_protocol_and_no_redirects(monkeypatch):
    seen = {}
    def handler(req):
        seen.update(json.loads(req.content))
        assert req.url.path == "/v1/systemone"
        assert req.headers["authorization"] == "Bearer synthetic-key"
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": {"x": {"type": "noul", "noul": 0.8}}, "usage": {"input_tokens": 10, "output_tokens": 2}})
    client_class = httpx.AsyncClient
    monkeypatch.setattr("app.decision.jev.httpx.AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    config = LLMProviderConfig(kind=ProviderKind.JEV, api_key_encrypted=encrypt("synthetic-key"))
    response = await JevProvider(config).decide(DecisionRequest("synthetic", {"x": "Is this synthetic?"}, "jev-1.13.0"))
    assert response.probabilities == {"x": 0.8}
    assert seen["questions"]["x"]["type"] == "noul"


@pytest.mark.parametrize("answer", [{}, {"x": {"type": "noul", "noul": True}}, {"x": {"type": "noul", "noul": 1.2}}, {"x": {"type": "choice", "noul": 0.8}}])
async def test_jev_rejects_invalid_answers(monkeypatch, answer):
    client_class = httpx.AsyncClient
    def handler(req):
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": answer, "usage": {"input_tokens": 10, "output_tokens": 2}})
    monkeypatch.setattr("app.decision.jev.httpx.AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    config = LLMProviderConfig(kind=ProviderKind.JEV, api_key_encrypted=encrypt("synthetic-key"))
    with pytest.raises(ProviderError, match="decision_invalid_response"):
        await JevProvider(config).decide(DecisionRequest("synthetic", {"x": "Question?"}, "jev-1.13.0"))


async def test_disabling_between_batches_stops_further_sends(db_session, monkeypatch):
    user, doc, config, _ = await setup_scan(db_session)
    await enable(db_session, config)
    monkeypatch.setattr("app.decision.service.MAX_BATCH_ITEMS", 1)
    class DisableAfterFirst(FakeDecision):
        async def decide(self, req):
            response = await super().decide(req)
            await save_settings(db_session, DecisionSettings(enabled=False, provider_config_id=config.id))
            return response
    fake = DisableAfterFirst()
    install(monkeypatch, fake)
    result = await run_scan(db_session, doc.id, user)
    assert len(fake.calls) == 1
    assert result.mode == "rules" and result.reasons == ["disabled"]


@pytest.mark.parametrize("status", [302, 401, 429, 500])
async def test_jev_http_failures_do_not_echo_response_or_follow_redirect(monkeypatch, status):
    client_class = httpx.AsyncClient
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, text="private upstream content", headers={"Location": "https://other.example/test"})
    monkeypatch.setattr("app.decision.jev.httpx.AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    config = LLMProviderConfig(kind=ProviderKind.JEV, api_key_encrypted=encrypt("synthetic-key"))
    with pytest.raises(ProviderError) as error:
        await JevProvider(config).decide(DecisionRequest("synthetic", {"x": "Question?"}, "jev-1.13.0"))
    assert str(error.value) == f"decision_http_{status}"
    assert len(calls) == 1
