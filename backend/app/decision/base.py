"""业务只认识概率判断协议，不依赖供应商的请求或响应格式。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DecisionRequest:
    state: str
    questions: dict[str, str]
    model: str


@dataclass(frozen=True)
class DecisionResponse:
    probabilities: dict[str, float]
    model: str
    tokens_in: int
    tokens_out: int


class DecisionProvider(Protocol):
    async def decide(self, request: DecisionRequest) -> DecisionResponse: ...
