from anthropic import AsyncAnthropic

from app.crypto import decrypt
from app.llm.models import LLMProviderConfig
from app.llm.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    ProviderError,
    classify,
)


class AnthropicProvider:
    def __init__(self, config: LLMProviderConfig) -> None:
        self._config = config
        self._client = AsyncAnthropic(
            api_key=decrypt(config.api_key_encrypted),
            base_url=config.base_url or None,
        )

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        try:
            message = await self._client.messages.create(
                model=req.model,
                system=req.system,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                messages=[{"role": "user", "content": req.prompt}],
            )
        except Exception as exc:  # noqa: BLE001 — 统一收敛为 ProviderError
            raise ProviderError(str(exc), retryable=classify(getattr(exc, "status_code", None)))

        text = "".join(block.text for block in message.content if block.type == "text")
        return CompletionResponse(
            text=text,
            tokens_in=message.usage.input_tokens,
            tokens_out=message.usage.output_tokens,
        )

    async def embed(self, req: EmbeddingRequest) -> EmbeddingResponse:
        # Anthropic 目前不提供 embedding 端点。embedding 任务应路由到
        # OpenAI 兼容 provider（设置页强制校验，见 Task 15）。
        raise ProviderError(
            "Anthropic 不提供 embedding 接口，请将 embedding 任务路由到其他 provider",
            retryable=False,
        )
