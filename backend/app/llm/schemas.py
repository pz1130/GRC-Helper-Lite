import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.models import ProviderKind, RulesetName


class ProviderCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: ProviderKind
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1)
    base_url: str | None = None
    is_fallback: bool = False
    monthly_budget: float | None = None


class ProviderUpdateIn(BaseModel):
    name: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    enabled: bool | None = None
    is_fallback: bool | None = None
    monthly_budget: float | None = None


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    kind: ProviderKind
    model: str
    base_url: str | None
    enabled: bool
    is_fallback: bool
    monthly_budget: float | None
    api_key_masked: str
    capabilities: list[str] = Field(default_factory=list)


class RoutingIn(BaseModel):
    task_key: str = Field(min_length=1, max_length=64)
    provider_config_id: int
    temperature: float = 0.0
    max_tokens: int = 4096


class BulkRoutingIn(BaseModel):
    """把一个 provider 绑到某 capability 下所有已实现的任务上。

    capability 必须显式传：一次调用只绑一类，绝不让聊天模型顺手把 embedding
    也绑走（见 app/llm/tasks.py 的说明）。
    """

    provider_config_id: int
    capability: Literal["chat", "embedding"]
    temperature: float = 0.0
    max_tokens: int = 4096


class TaskSpecOut(BaseModel):
    key: str
    capability: str
    implemented: bool


class RoutingOut(RoutingIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


class RedactionRuleIn(BaseModel):
    ruleset: RulesetName
    pattern_type: str = Field(pattern="^(regex|dictionary)$")
    pattern: str = Field(min_length=1)
    replacement_prefix: str = Field(min_length=1, max_length=32)
    enabled: bool = True
    order_index: int = 100
    note: str | None = None

    @model_validator(mode="after")
    def _regex_must_compile(self) -> "RedactionRuleIn":
        """非法正则要在这里挡下，不能等到跑 AI 的时候才炸。

        `load_engine` 对每条 regex 规则调 `re.compile`，抛出的 `re.error` 不是
        AppError、没有 code，于是抽取／映射／冲突／关系／审计答复全线失败，而批量
        任务是直接死掉、界面上不显示的。一个字符的笔误不该换掉整条 AI 管线。
        dictionary 类型是字面词表，不按正则解释，所以不参与这条校验。
        """
        if self.pattern_type == "regex":
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"not a valid regular expression: {exc}") from exc
        return self


class RedactionRuleOut(RedactionRuleIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


class PreviewIn(BaseModel):
    ruleset: RulesetName
    text: str


class PreviewOut(BaseModel):
    redacted: str
    hits: dict[str, int]


class ThresholdsIn(BaseModel):
    auto_accept_threshold: float = Field(ge=0.0, le=1.0)
    force_manual_threshold: float = Field(ge=0.0, le=1.0)
    review_sample_rate: float = Field(ge=0.0, le=1.0)
    monthly_budget_usd: float = Field(ge=0.0)


class ThresholdsOut(ThresholdsIn):
    pass
