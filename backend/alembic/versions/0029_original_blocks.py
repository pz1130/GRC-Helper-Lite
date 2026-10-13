"""独立保存原文块和条款出处，不随条款编辑改写。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("source_snapshot", sa.String(64), nullable=True))
    op.create_table(
        "original_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(200), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("document_id", "snapshot", "source_key", name="uq_original_block_key"),
    )
    op.create_index("ix_original_blocks_document_id", "original_blocks", ["document_id"])
    op.create_table(
        "clause_block_sources",
        sa.Column("clause_id", sa.Integer(), sa.ForeignKey("clauses.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("block_id", sa.Integer(), sa.ForeignKey("original_blocks.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_clause_block_sources_block_id", "clause_block_sources", ["block_id"])


def downgrade():
    op.drop_table("clause_block_sources")
    op.drop_table("original_blocks")
    op.drop_column("documents", "source_snapshot")
