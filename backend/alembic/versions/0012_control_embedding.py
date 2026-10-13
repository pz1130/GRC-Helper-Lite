"""Control-level retrieval vectors."""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.create_table(
        "control_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "control_id",
            sa.Integer(),
            sa.ForeignKey("controls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("embedding_model", sa.String(200)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("control_id", name="uq_embedding_per_control"),
    )
    op.create_index("ix_control_embeddings_control_id", "control_embeddings", ["control_id"])
    op.create_index("ix_control_embedding_model", "control_embeddings", ["embedding_model"])
    op.create_index(
        "ix_control_embedding",
        "control_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_table("control_embeddings")
