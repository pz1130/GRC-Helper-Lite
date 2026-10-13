"""Risk register."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    op.create_table(
        "risk_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", _enum("gap", "conflict", "manual", "audit_finding", name="risk_source"), nullable=False),
        sa.Column("source_ref", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("framework_item_id", sa.Integer(), sa.ForeignKey("framework_items.id", ondelete="SET NULL")),
        sa.Column("control_id", sa.Integer(), sa.ForeignKey("controls.id", ondelete="SET NULL")),
        sa.Column("likelihood", sa.Integer(), nullable=False),
        sa.Column("impact", sa.Integer(), nullable=False),
        sa.Column("inherent_score", sa.Integer(), nullable=False),
        sa.Column("mitigation", sa.Text(), nullable=False, server_default=""),
        sa.Column("residual_likelihood", sa.Integer()),
        sa.Column("residual_impact", sa.Integer()),
        sa.Column("residual_score", sa.Integer()),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("due_date", sa.Date()),
        sa.Column("status", _enum("open", "mitigating", "accepted", "closed", name="risk_status"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("likelihood BETWEEN 1 AND 5", name="ck_risk_likelihood"),
        sa.CheckConstraint("impact BETWEEN 1 AND 5", name="ck_risk_impact"),
        sa.CheckConstraint("residual_likelihood IS NULL OR residual_likelihood BETWEEN 1 AND 5", name="ck_risk_residual_likelihood"),
        sa.CheckConstraint("residual_impact IS NULL OR residual_impact BETWEEN 1 AND 5", name="ck_risk_residual_impact"),
    )
    op.create_index("ix_risk_entries_framework_item_id", "risk_entries", ["framework_item_id"])
    op.create_index("ix_risk_entries_control_id", "risk_entries", ["control_id"])
    op.create_index("ix_risk_entries_owner_user_id", "risk_entries", ["owner_user_id"])


def downgrade() -> None:
    op.drop_table("risk_entries")
