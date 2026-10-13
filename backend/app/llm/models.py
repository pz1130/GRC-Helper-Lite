from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin


class ProviderKind(StrEnum):
    JEV = "jev"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    GEMINI = "gemini"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    OLLAMA = "ollama"
    MINIMAX = "minimax"
    OPENAI_COMPATIBLE = "openai_compatible"


class RulesetName(StrEnum):
    """两套脱敏规则集（spec §6.2、D9）。

    GENERATION 完整脱敏；EMBEDDING 宽松，保留业务术语以免损伤检索精度。
    """

    GENERATION = "generation"
    EMBEDDING = "embedding"


class LLMProviderConfig(Base, TimestampMixin):
    __tablename__ = "llm_provider_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    kind: Mapped[ProviderKind] = mapped_column(
        SAEnum(
            ProviderKind,
            name="provider_kind",
            native_enum=False,
            length=32,
            values_callable=lambda kinds: [k.value for k in kinds],
            create_constraint=True,
        ),
        nullable=False,
    )
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    monthly_budget: Mapped[float | None] = mapped_column(Float, nullable=True)


class TaskRouting(Base, TimestampMixin):
    """task_key → provider 的路由。embedding 也是一个 task_key（spec §6.1）。"""

    __tablename__ = "llm_task_routing"
    __table_args__ = (UniqueConstraint("task_key", name="uq_task_routing_task_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_config_id: Mapped[int] = mapped_column(
        ForeignKey("llm_provider_config.id", ondelete="RESTRICT"), nullable=False
    )
    temperature: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096, nullable=False)


class LLMCall(Base):
    """每次调用留痕。可导出为合规证据（spec §8.2）。"""

    __tablename__ = "llm_call"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_provider_config.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    task_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # 这个模型不在价格表里，上面的 cost 是 0 但**不代表这次调用是免费的**。
    # 没有这个标记，未计价和真的零成本长得一模一样，预算闸门会静默放行。
    cost_unknown: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ruleset: Mapped[RulesetName] = mapped_column(
        SAEnum(
            RulesetName,
            name="ruleset_name",
            native_enum=False,
            length=32,
            values_callable=lambda names: [n.value for n in names],
            create_constraint=True,
        ),
        nullable=False,
    )
    redaction_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    redaction_hits: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class RedactionRule(Base, TimestampMixin):
    __tablename__ = "llm_redaction_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ruleset: Mapped[RulesetName] = mapped_column(
        SAEnum(
            RulesetName,
            name="ruleset_name",
            native_enum=False,
            length=32,
            values_callable=lambda names: [n.value for n in names],
            create_constraint=True,
        ),
        nullable=False,
        index=True,
    )
    # regex: pattern 是正则；dictionary: pattern 是换行分隔的词表
    pattern_type: Mapped[str] = mapped_column(String(16), nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    replacement_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AppSetting(Base, TimestampMixin):
    """键值配置。M1 用于存置信度阈值与月度预算（spec §7.4、D11）。"""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
