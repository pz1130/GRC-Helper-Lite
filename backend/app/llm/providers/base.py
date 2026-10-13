from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CompletionRequest:
    system: str
    prompt: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 4096


@dataclass(frozen=True)
class CompletionResponse:
    text: str
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class EmbeddingRequest:
    texts: list[str]
    model: str
    # 非对称向量：建库和检索用不同的编码方向。多数 provider 忽略它，
    # 但 MiniMax 等要求显式区分，用错方向会实打实地降低检索质量。
    purpose: str = "document"   # "document" | "query"


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: list[list[float]]
    tokens_in: int


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        self.retryable = retryable
        super().__init__(message)


class LLMProvider(Protocol):
    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...

    async def embed(self, req: EmbeddingRequest) -> EmbeddingResponse: ...


# 429/408/5xx 值得重试；401/403/400 重试也没用
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


def classify(status_code: int | None) -> bool:
    return status_code in RETRYABLE_STATUS if status_code is not None else True
