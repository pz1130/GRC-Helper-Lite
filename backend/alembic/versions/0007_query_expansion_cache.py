"""query expansion cache

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-08
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "query_expansion_cache",
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("terms", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("query_hash"),
    )


def downgrade() -> None:
    op.drop_table("query_expansion_cache")
