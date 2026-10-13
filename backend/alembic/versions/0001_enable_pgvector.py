"""enable pgvector extension

Revision ID: 0001
Revises:
Create Date: 2026-09-08
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # M3 才会真正用到向量列，但扩展在地基阶段就装好，
    # 避免后续迁移因缺扩展而失败。
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
