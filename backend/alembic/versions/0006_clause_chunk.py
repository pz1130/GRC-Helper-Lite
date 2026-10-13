"""clause chunks for hybrid retrieval

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-08
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clause_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clause_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("embedding_model", sa.String(length=200), nullable=True),
        sa.Column("embedding_version", sa.String(length=64), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column(
            "tsv",
            sa.dialects.postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=True,
        ),
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
        sa.ForeignKeyConstraint(["clause_id"], ["clauses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clause_id", "chunk_index", name="uq_chunk_per_clause"),
    )
    op.create_index(op.f("ix_clause_chunks_clause_id"), "clause_chunks", ["clause_id"])
    op.create_index("ix_chunk_document", "clause_chunks", ["document_id"])
    op.create_index("ix_chunk_embedding_model", "clause_chunks", ["embedding_model"])
    op.create_index("ix_chunk_tsv", "clause_chunks", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_chunk_embedding",
        "clause_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_chunk_embedding", table_name="clause_chunks")
    op.drop_index("ix_chunk_tsv", table_name="clause_chunks")
    op.drop_index("ix_chunk_embedding_model", table_name="clause_chunks")
    op.drop_index("ix_chunk_document", table_name="clause_chunks")
    op.drop_index(op.f("ix_clause_chunks_clause_id"), table_name="clause_chunks")
    op.drop_table("clause_chunks")
