import pytest
from sqlalchemy import select, text

from app.crypto import decrypt, encrypt
from app.llm.models import (
    AppSetting,
    LLMCall,
    LLMProviderConfig,
    ProviderKind,
    RedactionRule,
    RulesetName,
    TaskRouting,
)


@pytest.mark.asyncio
async def test_provider_config_stores_key_encrypted(db_session):
    cfg = LLMProviderConfig(
        name="prod-anthropic",
        kind=ProviderKind.ANTHROPIC,
        model="claude-opus-5",
        api_key_encrypted=encrypt("sk-ant-secret"),
    )
    db_session.add(cfg)
    await db_session.flush()

    raw_kind = await db_session.scalar(
        text("SELECT kind FROM llm_provider_config WHERE name = :name"),
        {"name": "prod-anthropic"},
    )
    assert raw_kind == "anthropic"

    db_session.expire(cfg)
    found = await db_session.scalar(select(LLMProviderConfig))
    assert found.kind is ProviderKind.ANTHROPIC
    assert "sk-ant-secret" not in found.api_key_encrypted
    assert decrypt(found.api_key_encrypted) == "sk-ant-secret"
    assert found.enabled is True
    assert found.is_fallback is False


@pytest.mark.asyncio
async def test_task_routing_task_key_is_unique(db_session):
    from sqlalchemy.exc import IntegrityError

    cfg = LLMProviderConfig(
        name="p", kind=ProviderKind.ANTHROPIC, model="claude-opus-5",
        api_key_encrypted=encrypt("k"),
    )
    db_session.add(cfg)
    await db_session.flush()

    db_session.add(TaskRouting(task_key="control_extract", provider_config_id=cfg.id))
    await db_session.flush()
    db_session.add(TaskRouting(task_key="control_extract", provider_config_id=cfg.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_llm_call_records_redaction_facts(db_session):
    call = LLMCall(
        model="claude-opus-5",
        task_key="control_extract",
        prompt_hash="abc123",
        tokens_in=100,
        tokens_out=50,
        cost=0.0123,
        latency_ms=850,
        ruleset=RulesetName.GENERATION,
        redaction_applied=True,
        redaction_hits={"ORG": 2, "PERSON": 1},
        status="ok",
    )
    db_session.add(call)
    await db_session.flush()

    raw_ruleset = await db_session.scalar(
        text("SELECT ruleset FROM llm_call WHERE prompt_hash = :prompt_hash"),
        {"prompt_hash": "abc123"},
    )
    assert raw_ruleset == "generation"

    db_session.expire(call)
    found = await db_session.scalar(select(LLMCall))
    assert found.ruleset is RulesetName.GENERATION
    assert found.redaction_applied is True
    assert found.redaction_hits == {"ORG": 2, "PERSON": 1}


@pytest.mark.asyncio
async def test_redaction_rule_belongs_to_a_ruleset(db_session):
    rule = RedactionRule(
        ruleset=RulesetName.EMBEDDING,
        pattern_type="regex",
        pattern=r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
        replacement_prefix="IP",
        order_index=10,
    )
    db_session.add(rule)
    await db_session.flush()

    found = await db_session.scalar(
        select(RedactionRule).where(RedactionRule.id == rule.id)
    )
    assert found.ruleset is RulesetName.EMBEDDING
    assert found.enabled is True


@pytest.mark.asyncio
async def test_app_setting_is_key_value(db_session):
    db_session.add(AppSetting(key="custom_threshold", value={"value": 0.9}))
    await db_session.flush()
    found = await db_session.scalar(
        select(AppSetting).where(AppSetting.key == "custom_threshold")
    )
    assert found.value == {"value": 0.9}
