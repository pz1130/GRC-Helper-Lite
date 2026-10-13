"""Clause split/merge: keep the loser row."""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clauses",
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
    )
    op.add_column("clauses", sa.Column("merged_into_id", sa.Integer(), nullable=True))
    op.create_index("ix_clauses_merged_into_id", "clauses", ["merged_into_id"])
    op.create_foreign_key(
        "fk_clauses_merged_into_id",
        "clauses",
        "clauses",
        ["merged_into_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_clauses_merged_into_id", "clauses", type_="foreignkey")
    op.drop_index("ix_clauses_merged_into_id", table_name="clauses")
    op.drop_column("clauses", "merged_into_id")
    op.drop_column("clauses", "status")
