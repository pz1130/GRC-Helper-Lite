"""allow minimax provider kind

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-08
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS = (
    "anthropic",
    "openai",
    "azure_openai",
    "gemini",
    "deepseek",
    "qwen",
    "ollama",
    "minimax",
    "openai_compatible",
)
_OLD = tuple(k for k in _KINDS if k != "minimax")


def _values(kinds: tuple[str, ...]) -> str:
    return ", ".join(f"'{kind}'" for kind in kinds)


def upgrade() -> None:
    # 枚举列带 CHECK 约束（M2 补的），加新取值必须同时放宽约束，
    # 否则插入新 kind 会被数据库拒掉。
    op.drop_constraint("provider_kind", "llm_provider_config", type_="check")
    op.create_check_constraint(
        "provider_kind", "llm_provider_config", f"kind IN ({_values(_KINDS)})"
    )


def downgrade() -> None:
    op.execute("DELETE FROM llm_provider_config WHERE kind = 'minimax'")
    op.drop_constraint("provider_kind", "llm_provider_config", type_="check")
    op.create_check_constraint(
        "provider_kind", "llm_provider_config", f"kind IN ({_values(_OLD)})"
    )
