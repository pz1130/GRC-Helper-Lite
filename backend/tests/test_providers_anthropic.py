from unittest.mock import AsyncMock, patch

import pytest

from app.crypto import encrypt
from app.llm.models import LLMProviderConfig, ProviderKind
from app.llm.pricing import estimate_cost
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.base import CompletionRequest, ProviderError


def _config() -> LLMProviderConfig:
    return LLMProviderConfig(
        id=1,
        name="prod",
        kind=ProviderKind.ANTHROPIC,
        model="claude-opus-5",
        api_key_encrypted=encrypt("sk-ant-test"),
    )


class _FakeMessage:
    def __init__(self) -> None:
        self.content = [type("Block", (), {"type": "text", "text": '{"ok": true}'})()]
        self.usage = type("Usage", (), {"input_tokens": 120, "output_tokens": 34})()


@pytest.mark.asyncio
async def test_complete_returns_text_and_token_counts():
    provider = AnthropicProvider(_config())
    with patch.object(
        provider._client.messages, "create", new=AsyncMock(return_value=_FakeMessage())
    ):
        resp = await provider.complete(
            CompletionRequest(system="s", prompt="p", model="claude-opus-5")
        )
    assert resp.text == '{"ok": true}'
    assert resp.tokens_in == 120
    assert resp.tokens_out == 34


@pytest.mark.asyncio
async def test_rate_limit_is_marked_retryable():
    provider = AnthropicProvider(_config())

    class _RateLimit(Exception):
        status_code = 429

    with patch.object(
        provider._client.messages, "create", new=AsyncMock(side_effect=_RateLimit("429"))
    ):
        with pytest.raises(ProviderError) as exc:
            await provider.complete(
                CompletionRequest(system="s", prompt="p", model="claude-opus-5")
            )
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_auth_error_is_not_retryable():
    provider = AnthropicProvider(_config())

    class _AuthError(Exception):
        status_code = 401

    with patch.object(
        provider._client.messages, "create", new=AsyncMock(side_effect=_AuthError("401"))
    ):
        with pytest.raises(ProviderError) as exc:
            await provider.complete(
                CompletionRequest(system="s", prompt="p", model="claude-opus-5")
            )
    assert exc.value.retryable is False


def test_estimate_cost_for_known_model():
    # 100 万 in / 100 万 out 应等于该模型的单价之和
    assert estimate_cost("claude-opus-5", 1_000_000, 1_000_000) == pytest.approx(
        estimate_cost("claude-opus-5", 1_000_000, 0)
        + estimate_cost("claude-opus-5", 0, 1_000_000)
    )


def test_estimate_cost_for_unknown_model_is_zero_not_crash():
    """未知模型不能让整条流水线挂掉，成本记 0 并由运维在价格表补齐。"""
    assert estimate_cost("some-local-model", 1000, 1000) == 0.0
