"""Render-version marker for control embeddings."""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "control_embeddings", sa.Column("embedding_version", sa.String(20), nullable=True)
    )
    # 既有向量出自 v1 渲染（code + title + statement）。标成 v1 而不是留空，
    # 下一次 embed_pending 才会把它们判成 stale 并重算。
    op.execute("UPDATE control_embeddings SET embedding_version = 'v1'")


def downgrade() -> None:
    op.drop_column("control_embeddings", "embedding_version")
