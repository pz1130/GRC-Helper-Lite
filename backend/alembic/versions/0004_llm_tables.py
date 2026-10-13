"""llm tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-08
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "llm_provider_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "anthropic",
                "openai",
                "azure_openai",
                "gemini",
                "deepseek",
                "qwen",
                "ollama",
                "openai_compatible",
                name="provider_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("is_fallback", sa.Boolean(), nullable=False),
        sa.Column("monthly_budget", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "llm_redaction_rule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "ruleset",
            sa.Enum(
                "generation",
                "embedding",
                name="ruleset_name",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("pattern_type", sa.String(length=16), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("replacement_prefix", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_llm_redaction_rule_ruleset"),
        "llm_redaction_rule",
        ["ruleset"],
        unique=False,
    )
    op.create_table(
        "llm_call",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_config_id", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("task_key", sa.String(length=64), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "ruleset",
            sa.Enum(
                "generation",
                "embedding",
                name="ruleset_name",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("redaction_applied", sa.Boolean(), nullable=False),
        sa.Column("redaction_hits", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["provider_config_id"], ["llm_provider_config.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_call_at"), "llm_call", ["at"], unique=False)
    op.create_index(op.f("ix_llm_call_task_key"), "llm_call", ["task_key"], unique=False)
    op.create_table(
        "llm_task_routing",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_key", sa.String(length=64), nullable=False),
        sa.Column("provider_config_id", sa.Integer(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["provider_config_id"], ["llm_provider_config.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_key", name="uq_task_routing_task_key"),
    )

    # 默认脱敏规则：generation 完整，embedding 宽松（保留业务术语）
    op.execute("""
        INSERT INTO llm_redaction_rule
            (ruleset, pattern_type, pattern, replacement_prefix, enabled, order_index, note,
             created_at, updated_at)
        VALUES
            ('generation', 'regex', '\\b\\d{1,3}(?:\\.\\d{1,3}){3}\\b', 'IP', true, 10,
             'IPv4 地址', now(), now()),
            ('generation', 'regex', '[A-Za-z0-9._%%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}', 'EMAIL',
             true, 20, '邮箱地址', now(), now()),
            ('embedding', 'regex', '\\b\\d{1,3}(?:\\.\\d{1,3}){3}\\b', 'IP', true, 10,
             'IPv4 地址', now(), now()),
            ('embedding', 'regex', '[A-Za-z0-9._%%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}', 'EMAIL',
             true, 20, '邮箱地址', now(), now())
    """)

    # 确认队列阈值（spec §7.4、D11），M4 消费
    op.execute("""
        INSERT INTO app_setting (key, value, created_at, updated_at) VALUES
            ('auto_accept_threshold', '{"value": 0.90}', now(), now()),
            ('force_manual_threshold', '{"value": 0.60}', now(), now()),
            ('monthly_budget_usd', '{"value": 200}', now(), now())
    """)

    # 补 Task 4 遗漏的授权列约束（见本任务开头的说明）
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('admin', 'grc_lead', 'contributor', 'viewer')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_table("llm_task_routing")
    op.drop_index(op.f("ix_llm_call_task_key"), table_name="llm_call")
    op.drop_index(op.f("ix_llm_call_at"), table_name="llm_call")
    op.drop_table("llm_call")
    op.drop_index(op.f("ix_llm_redaction_rule_ruleset"), table_name="llm_redaction_rule")
    op.drop_table("llm_redaction_rule")
    op.drop_table("llm_provider_config")
    op.drop_table("app_setting")
