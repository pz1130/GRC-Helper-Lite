from app.llm.models import LLMProviderConfig, ProviderKind
from app.llm.providers.base import LLMProvider

DEFAULT_BASE_URLS: dict[ProviderKind, str] = {
    ProviderKind.JEV: "https://api.typesafe.ai/v1",
    ProviderKind.OPENAI: "https://api.openai.com/v1",
    ProviderKind.ANTHROPIC: "https://api.anthropic.com",
    ProviderKind.GEMINI: "https://generativelanguage.googleapis.com/v1beta/openai",
    ProviderKind.DEEPSEEK: "https://api.deepseek.com/v1",
    ProviderKind.QWEN: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ProviderKind.OLLAMA: "http://host.docker.internal:11434/v1",
    # MiniMax 中国站。国际站是 https://api.minimax.io/v1
    ProviderKind.MINIMAX: "https://api.minimaxi.com/v1",
}


def build_provider(config: LLMProviderConfig) -> LLMProvider:
    from app.decision.factory import supports_decisions
    from app.llm.providers.anthropic import AnthropicProvider
    from app.llm.providers.base import ProviderError
    from app.llm.providers.minimax import MiniMaxProvider
    from app.llm.providers.openai_compat import OpenAICompatProvider

    if supports_decisions(config.kind):
        raise ProviderError("Decision providers do not support chat or embeddings", retryable=False)
    if config.kind is ProviderKind.ANTHROPIC:
        return AnthropicProvider(config)
    if config.kind is ProviderKind.MINIMAX:
        # chat 兼容 OpenAI，但 embedding 的请求/响应/错误表示都不同
        return MiniMaxProvider(config)
    return OpenAICompatProvider(config)
