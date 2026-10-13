"""Unified review proposals."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "kind",
            sa.Enum(
                "control_extract",
                "mapping",
                "relation",
                "conflict",
                "answer",
                "maturity_score",
                "evidence_suggestion",
                "audit_prediction",
                "matrix_mapping",
                name="proposal_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("llm_call_id", sa.Integer(), sa.ForeignKey("llm_call.id", ondelete="SET NULL")),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE")),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "accepted",
                "modified",
                "rejected",
                name="proposal_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_payload", postgresql.JSONB()),
        sa.Column("reject_reason", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_proposal_queue", "proposals", ["status", "confidence"])
    op.create_index("ix_proposal_document", "proposals", ["document_id"])


def downgrade() -> None:
    op.drop_table("proposals")
