"""Policy conflicts."""

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("clause_a_id", sa.Integer(), sa.ForeignKey("clauses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("clause_b_id", sa.Integer(), sa.ForeignKey("clauses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic", sa.String(200), nullable=False),
        sa.Column("difference", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confirmed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("clause_a_id", "clause_b_id", "topic", name="uq_policy_conflict"),
    )
    op.create_index("ix_policy_conflicts_clause_a_id", "policy_conflicts", ["clause_a_id"])
    op.create_index("ix_policy_conflicts_clause_b_id", "policy_conflicts", ["clause_b_id"])


def downgrade() -> None:
    op.drop_index("ix_policy_conflicts_clause_b_id", table_name="policy_conflicts")
    op.drop_index("ix_policy_conflicts_clause_a_id", table_name="policy_conflicts")
    op.drop_table("policy_conflicts")
