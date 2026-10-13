import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.crypto import encrypt
from app.decision.base import DecisionResponse
from app.decision.settings import DecisionSettings, save_settings
from app.iam.models import AuditLog
from app.llm.models import AppSetting, LLMCall, LLMProviderConfig, ProviderKind
from app.mapping import jev_prefilter

ITEMS = [(1, "Identities and credentials are managed")]
CONTROLS = [(10, "Backup"), (11, "Account management"), (12, "Media sanitisation")]


@pytest.fixture
def flag(monkeypatch):
    def set_flag(value: bool):
        monkeypatch.setattr(jev_prefilter, "get_settings",
                            lambda: SimpleNamespace(mapping_jev_prefilter=value))
    set_flag(True)
    return set_flag


async def _jev(db_session) -> LLMProviderConfig:
    config = LLMProviderConfig(name="jev", kind=ProviderKind.JEV, model="jev-1.13.0",
                               api_key_encrypted=encrypt("k"))
    db_session.add(config)
    await db_session.flush()
    await save_settings(db_session, DecisionSettings(enabled=False, provider_config_id=config.id))
    return config


class FakeJev:
    def __init__(self, behaviour="ok"):
        self.behaviour = behaviour
        self.requests = []

    async def decide(self, request):
        self.requests.append(request)
        if self.behaviour == "error":
            raise RuntimeError("upstream echoed secret text")
        if self.behaviour == "hang":
            await asyncio.sleep(5)
        probs = {"c10": 0.1, "c11": 0.9, "c12": 0.4}
        return DecisionResponse({k: probs[k] for k in request.questions}, "jev-1.13.0", 100, 10)


def _use(monkeypatch, fake):
    monkeypatch.setattr(jev_prefilter, "build_decision_provider", lambda config: fake)


@pytest.mark.asyncio
async def test_flag_off_never_calls_jev(db_session, flag, monkeypatch):
    flag(False)
    await _jev(db_session)
    fake = FakeJev()
    _use(monkeypatch, fake)
    result, reason = await jev_prefilter.rank(db_session, ITEMS, CONTROLS, top_k=2)
    assert result is None and reason == "disabled"
    assert fake.requests == []
    assert await db_session.scalar(select(LLMCall)) is None


@pytest.mark.asyncio
async def test_flag_on_without_a_jev_provider_falls_back(db_session, flag):
    result, reason = await jev_prefilter.rank(db_session, ITEMS, CONTROLS, top_k=2)
    assert result is None and reason == "not_configured"


@pytest.mark.asyncio
async def test_flag_on_ranks_by_jev_probability_and_records_the_call(db_session, flag, monkeypatch):
    config = await _jev(db_session)
    fake = FakeJev()
    _use(monkeypatch, fake)
    result, reason = await jev_prefilter.rank(db_session, ITEMS, CONTROLS, top_k=2)
    assert reason is None
    assert result == {1: [11, 12]}
    call = await db_session.scalar(select(LLMCall).where(LLMCall.task_key == jev_prefilter.TASK_KEY))
    assert call.status == "ok" and call.provider_config_id == config.id
    assert call.tokens_in == 100


@pytest.mark.asyncio
async def test_provider_error_falls_back_with_audit_and_no_upstream_text(db_session, flag, monkeypatch):
    await _jev(db_session)
    _use(monkeypatch, FakeJev("error"))
    result, reason = await jev_prefilter.rank(db_session, ITEMS, CONTROLS, top_k=2)
    assert result is None and reason == "call_failed"
    call = await db_session.scalar(select(LLMCall).where(LLMCall.task_key == jev_prefilter.TASK_KEY))
    assert call.status == "error" and call.error == "call_failed"
    audit = await db_session.scalar(select(AuditLog).where(AuditLog.action == "mapping.prefilter_fallback"))
    assert audit.after == {"reason": "call_failed", "fallback": "keyword_vector"}


@pytest.mark.asyncio
async def test_timeout_falls_back(db_session, flag, monkeypatch):
    await _jev(db_session)
    _use(monkeypatch, FakeJev("hang"))
    monkeypatch.setattr(jev_prefilter, "CALL_TIMEOUT_SECONDS", 0.05)
    result, reason = await jev_prefilter.rank(db_session, ITEMS, CONTROLS, top_k=2)
    assert result is None and reason == "timeout"


@pytest.mark.asyncio
async def test_budget_exhausted_falls_back_before_sending(db_session, flag, monkeypatch):
    await _jev(db_session)
    fake = FakeJev()
    _use(monkeypatch, fake)
    setting = await db_session.get(AppSetting, "monthly_budget_usd")
    setting.value = {"value": 1.0}
    db_session.add(LLMCall(provider_config_id=None, model="m", task_key="x", prompt_hash="h",
                           cost=5.0, ruleset="generation", status="ok"))
    await db_session.flush()
    result, reason = await jev_prefilter.rank(db_session, ITEMS, CONTROLS, top_k=2)
    assert result is None and reason == "budget_exhausted"
    assert fake.requests == []


@pytest.mark.asyncio
async def test_controls_are_split_into_bounded_calls(db_session, flag, monkeypatch):
    await _jev(db_session)
    monkeypatch.setattr(jev_prefilter, "MAX_QUESTIONS", 2)
    fake = FakeJev()
    _use(monkeypatch, fake)
    result, _ = await jev_prefilter.rank(db_session, ITEMS, CONTROLS, top_k=3)
    assert len(fake.requests) == 2
    assert result == {1: [11, 12, 10]}
