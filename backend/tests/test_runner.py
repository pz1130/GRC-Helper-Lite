from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.crypto import encrypt
from app.llm.models import (
    LLMCall,
    LLMProviderConfig,
    ProviderKind,
    RedactionRule,
    RulesetName,
    TaskRouting,
)
from app.llm.providers.base import CompletionResponse, ProviderError
from app.llm.runner import run

SCHEMA = {
    "type": "object",
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}, "confidence": {"type": "number"}},
}


async def _setup(db_session, *, fallback: bool = False) -> LLMProviderConfig:
    primary = LLMProviderConfig(
        name="primary", kind=ProviderKind.ANTHROPIC, model="claude-opus-5",
        api_key_encrypted=encrypt("sk-primary"),
    )
    db_session.add(primary)
    if fallback:
        db_session.add(
            LLMProviderConfig(
                name="backup", kind=ProviderKind.ANTHROPIC, model="claude-sonnet-5",
                api_key_encrypted=encrypt("sk-backup"), is_fallback=True,
            )
        )
    db_session.add(
        RedactionRule(
            ruleset=RulesetName.GENERATION, pattern_type="regex",
            pattern=r"\b\d{1,3}(?:\.\d{1,3}){3}\b", replacement_prefix="IP", order_index=10,
        )
    )
    await db_session.flush()
    db_session.add(TaskRouting(task_key="answer_generation", provider_config_id=primary.id))
    await db_session.flush()
    return primary


@pytest.mark.asyncio
async def test_run_returns_validated_payload(db_session):
    await _setup(db_session)
    with patch(
        "app.llm.runner.build_provider",
        return_value=AsyncMock(
            complete=AsyncMock(
                return_value=CompletionResponse(
                    text='{"answer": "已实施", "confidence": 0.9}', tokens_in=10, tokens_out=5
                )
            )
        ),
    ):
        result = await run(
            db_session, task_key="answer_generation", system="s", prompt="p", schema=SCHEMA
        )
    assert result.payload["answer"] == "已实施"
    assert result.confidence == 0.9


@pytest.mark.asyncio
async def test_prompt_is_redacted_before_leaving(db_session):
    """铁律：出网内容必须先脱敏。"""
    await _setup(db_session)
    fake = AsyncMock(
        complete=AsyncMock(
            return_value=CompletionResponse(text='{"answer": "ok"}', tokens_in=1, tokens_out=1)
        )
    )
    with patch("app.llm.runner.build_provider", return_value=fake):
        await run(
            db_session,
            task_key="answer_generation",
            system="s",
            prompt="跳板机 10.20.30.40 需复核",
            schema=SCHEMA,
        )
    sent = fake.complete.call_args.args[0].prompt
    assert "10.20.30.40" not in sent
    assert "[[IP_1]]" in sent


@pytest.mark.asyncio
async def test_response_is_restored_after_return(db_session):
    await _setup(db_session)
    with patch(
        "app.llm.runner.build_provider",
        return_value=AsyncMock(
            complete=AsyncMock(
                return_value=CompletionResponse(
                    text='{"answer": "请检查 [[IP_1]] 的日志"}', tokens_in=1, tokens_out=1
                )
            )
        ),
    ):
        result = await run(
            db_session,
            task_key="answer_generation",
            system="s",
            prompt="跳板机 10.20.30.40 需复核",
            schema=SCHEMA,
        )
    assert result.payload["answer"] == "请检查 10.20.30.40 的日志"


@pytest.mark.asyncio
async def test_schema_failure_triggers_correction_retry(db_session):
    await _setup(db_session)
    complete = AsyncMock(
        side_effect=[
            CompletionResponse(text='{"wrong": 1}', tokens_in=1, tokens_out=1),
            CompletionResponse(text='{"answer": "修好了"}', tokens_in=1, tokens_out=1),
        ]
    )
    with patch("app.llm.runner.build_provider", return_value=AsyncMock(complete=complete)):
        result = await run(
            db_session, task_key="answer_generation", system="s", prompt="p", schema=SCHEMA
        )
    assert result.payload["answer"] == "修好了"
    assert complete.await_count == 2


@pytest.mark.asyncio
async def test_persistent_schema_failure_raises_after_two_retries(db_session):
    from app.llm.validation import ValidationFailure

    await _setup(db_session)
    complete = AsyncMock(
        return_value=CompletionResponse(text='{"wrong": 1}', tokens_in=1, tokens_out=1)
    )
    with patch("app.llm.runner.build_provider", return_value=AsyncMock(complete=complete)):
        with pytest.raises(ValidationFailure):
            await run(
                db_session, task_key="answer_generation", system="s", prompt="p", schema=SCHEMA
            )
    assert complete.await_count == 3   # 首次 + 2 次纠错


@pytest.mark.asyncio
async def test_retryable_provider_error_falls_back(db_session):
    await _setup(db_session, fallback=True)
    primary = AsyncMock(complete=AsyncMock(side_effect=ProviderError("429", retryable=True)))
    backup = AsyncMock(
        complete=AsyncMock(
            return_value=CompletionResponse(text='{"answer": "备用"}', tokens_in=1, tokens_out=1)
        )
    )
    with patch("app.llm.runner.build_provider", side_effect=[primary, backup]):
        with patch("app.llm.runner._sleep", new=AsyncMock()):
            result = await run(
                db_session, task_key="answer_generation", system="s", prompt="p", schema=SCHEMA
            )
    assert result.payload["answer"] == "备用"


@pytest.mark.asyncio
async def test_llm_call_is_recorded_with_redaction_facts(db_session):
    await _setup(db_session)
    with patch(
        "app.llm.runner.build_provider",
        return_value=AsyncMock(
            complete=AsyncMock(
                return_value=CompletionResponse(text='{"answer": "ok"}', tokens_in=10, tokens_out=5)
            )
        ),
    ):
        await run(
            db_session,
            task_key="answer_generation",
            system="s",
            prompt="主机 10.0.0.1",
            schema=SCHEMA,
        )
    call = await db_session.scalar(select(LLMCall))
    assert call.status == "ok"
    assert call.redaction_applied is True
    assert call.redaction_hits == {"IP": 1}
    assert call.tokens_in == 10
    assert call.cost > 0


@pytest.mark.asyncio
async def test_failed_call_is_also_recorded(db_session):
    await _setup(db_session)
    with patch(
        "app.llm.runner.build_provider",
        return_value=AsyncMock(complete=AsyncMock(side_effect=ProviderError("401", retryable=False))),
    ):
        with pytest.raises(ProviderError):
            await run(
                db_session, task_key="answer_generation", system="s", prompt="p", schema=SCHEMA
            )
    call = await db_session.scalar(select(LLMCall))
    assert call.status == "error"
    assert "401" in call.error


@pytest.mark.asyncio
async def test_citation_validator_rejection_raises(db_session):
    from app.llm.validation import ValidationFailure

    class _AlwaysReject:
        async def check(self, payload):
            return "引用的条款不存在"

    await _setup(db_session)
    provider = AsyncMock(
        complete=AsyncMock(
            return_value=CompletionResponse(text='{"answer": "ok"}', tokens_in=1, tokens_out=1)
        )
    )
    with patch(
        "app.llm.runner.build_provider",
        return_value=provider,
    ), pytest.raises(ValidationFailure, match="引用的条款不存在"):
        await run(
            db_session,
            task_key="answer_generation",
            system="s",
            prompt="p",
            schema=SCHEMA,
            citation_validator=_AlwaysReject(),
        )
    assert provider.complete.await_count == 3


@pytest.mark.asyncio
async def test_run_does_not_write_business_tables(db_session):
    """铁律 2：run() 只返回纯数据，把它变成 Proposal 是 M4 的事。"""
    import app.llm.runner as runner_module

    source = open(runner_module.__file__, encoding="utf-8").read()
    for forbidden in ("Proposal", "Control(", "Document("):
        assert forbidden not in source, f"runner 不应引用业务实体 {forbidden}"


@pytest.mark.asyncio
async def test_system_and_prompt_entities_do_not_collide(db_session):
    """system 与 prompt 各含一个不同 IP 时，还原必须各归各位。

    分两次 redact 的写法下，两边都是 [[IP_1]]，合并映射表时撞掉一个，
    模型引用 prompt 里那个 IP 会被还原成 system 里的——静默且看着合理。
    """
    await _setup(db_session)
    fake = AsyncMock(
        complete=AsyncMock(
            return_value=CompletionResponse(
                text='{"answer": "请核查 [[IP_2]] 与 [[IP_1]]"}', tokens_in=1, tokens_out=1
            )
        )
    )
    with patch("app.llm.runner.build_provider", return_value=fake):
        result = await run(
            db_session,
            task_key="answer_generation",
            system="本行堡垒机是 192.168.1.1",
            prompt="请核查 10.20.30.40 的访问日志",
            schema=SCHEMA,
        )

    sent = fake.complete.call_args.args[0]
    assert "192.168.1.1" not in sent.system
    assert "10.20.30.40" not in sent.prompt
    assert sent.system != sent.prompt
    # IP_1 来自 system，IP_2 来自 prompt，各自还原到自己的原文
    assert result.payload["answer"] == "请核查 10.20.30.40 与 192.168.1.1"


@pytest.mark.asyncio
async def test_redaction_hits_cover_both_system_and_prompt(db_session):
    await _setup(db_session)
    with patch(
        "app.llm.runner.build_provider",
        return_value=AsyncMock(
            complete=AsyncMock(
                return_value=CompletionResponse(text='{"answer": "ok"}', tokens_in=1, tokens_out=1)
            )
        ),
    ):
        await run(
            db_session,
            task_key="answer_generation",
            system="堡垒机 192.168.1.1",
            prompt="主机 10.0.0.1 与 10.0.0.2",
            schema=SCHEMA,
        )
    call = await db_session.scalar(select(LLMCall))
    assert call.redaction_hits == {"IP": 3}, "system 里那个实体不能漏统计"


@pytest.mark.asyncio
async def test_failed_embedding_is_also_recorded(db_session):
    """spec §8.2：每次调用都要留痕，失败的也算。"""
    from app.llm.runner import embed

    primary = await _setup(db_session)
    db_session.add(TaskRouting(task_key="embedding", provider_config_id=primary.id))
    await db_session.flush()

    with patch(
        "app.llm.runner.build_provider",
        return_value=AsyncMock(
            embed=AsyncMock(side_effect=ProviderError("上游返回 500", retryable=True))
        ),
    ), pytest.raises(ProviderError):
        await embed(db_session, texts=["条款正文"])

    call = await db_session.scalar(
        select(LLMCall).where(LLMCall.task_key == "embedding")
    )
    assert call is not None
    assert call.status == "error"
    assert "500" in call.error


@pytest.mark.asyncio
async def test_a_hung_provider_call_hits_the_total_deadline(monkeypatch):
    """httpx 的读超时只管「多久没收到字节」，服务端零星吐字节就永远不触发。

    实测：600s 读超时下，一次调用挂了 100 分钟仍未返回，整轮标定就此停摆，
    而事务和 advisory 锁被一起攥住。总时限必须在调用外面加。
    """
    import asyncio

    from app.llm import runner
    from app.llm.providers.base import CompletionRequest, ProviderError

    class Hanging:
        async def complete(self, request):
            await asyncio.sleep(3600)

    monkeypatch.setattr(runner, "build_provider", lambda config: Hanging())
    monkeypatch.setattr(runner, "REQUEST_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(runner, "_sleep", AsyncMock())
    monkeypatch.setattr(runner.routing, "fallback_provider", AsyncMock(return_value=None))

    config = SimpleNamespace(id=1, model="m")
    with pytest.raises(ProviderError) as excinfo:
        await runner._call_with_retry(
            MagicMock(), config,
            CompletionRequest(system="s", prompt="p", model="m",
                              temperature=0.0, max_tokens=16),
        )
    assert "总时限" in str(excinfo.value)
    assert excinfo.value.retryable is True
