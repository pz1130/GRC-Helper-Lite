"""保留两阶段抽取的要求清单与逐条检查结果。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "extraction_inventories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("llm_call_id", sa.Integer(), sa.ForeignKey("llm_call.id"), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_extraction_inventories_document_id", "extraction_inventories", ["document_id"])
    op.create_index("ix_extraction_inventories_fingerprint", "extraction_inventories", ["fingerprint"])


def downgrade():
    op.drop_table("extraction_inventories")
