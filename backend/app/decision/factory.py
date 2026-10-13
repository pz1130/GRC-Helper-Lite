"""新增判断服务时只在这里注册适配器；业务与配置界面按能力选择。"""

from app.decision.base import DecisionProvider
from app.decision.jev import JevProvider
from app.llm.models import LLMProviderConfig, ProviderKind
from app.llm.providers.base import ProviderError

ADAPTERS = {ProviderKind.JEV: JevProvider}


def supports_decisions(kind: ProviderKind) -> bool:
    return kind in ADAPTERS


def build_decision_provider(config: LLMProviderConfig) -> DecisionProvider:
    adapter = ADAPTERS.get(config.kind)
    if adapter is None:
        raise ProviderError("decision_unsupported_provider", retryable=False)
    return adapter(config)
