"""Add optional decision providers and document decision snapshots."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

KINDS = "'anthropic','openai','azure_openai','gemini','deepseek','qwen','ollama','minimax','openai_compatible'"


def upgrade():
    op.drop_constraint("provider_kind", "llm_provider_config", type_="check")
    op.create_check_constraint("provider_kind", "llm_provider_config", f"kind IN ({KINDS},'jev')")
    op.create_table(
        "decision_scans",
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("probabilities", postgresql.JSONB(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    # 有判断服务配置时拒绝回退，不能静默删掉已加密的凭据与路由。
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM llm_provider_config WHERE kind='jev'")):
        raise RuntimeError("Remove decision provider configurations before downgrading")
    connection.execute(sa.text("DELETE FROM llm_task_routing WHERE task_key='requirement_decision'"))
    connection.execute(sa.text("DELETE FROM app_setting WHERE key='decision_model'"))
    op.drop_table("decision_scans")
    op.drop_constraint("provider_kind", "llm_provider_config", type_="check")
    op.create_check_constraint("provider_kind", "llm_provider_config", f"kind IN ({KINDS})")
