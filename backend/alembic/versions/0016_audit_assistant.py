"""Audit engagements, questions, and answer drafts."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    op.create_table(
        "audit_engagements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("audit_type", _enum("internal", "external", "regulatory", name="audit_type"), nullable=False),
        sa.Column("framework_id", sa.Integer(), sa.ForeignKey("frameworks.id", ondelete="SET NULL")),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date()),
        sa.Column("status", _enum("preparing", "ongoing", "closed", name="audit_engagement_status"), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_engagements_framework_id", "audit_engagements", ["framework_id"])
    op.create_table(
        "audit_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("engagement_id", sa.Integer(), sa.ForeignKey("audit_engagements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("framework_item_id", sa.Integer(), sa.ForeignKey("framework_items.id", ondelete="SET NULL")),
        sa.Column("status", _enum("pending", "drafted", "finalized", name="audit_question_status"), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("engagement_id", "seq", name="uq_audit_question_seq"),
    )
    op.create_index("ix_audit_questions_engagement_id", "audit_questions", ["engagement_id"])
    op.create_index("ix_audit_questions_framework_item_id", "audit_questions", ["framework_item_id"])
    op.create_table(
        "answer_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("audit_questions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("cited_clause_ids", postgresql.JSONB(), nullable=False),
        sa.Column("cited_control_ids", postgresql.JSONB(), nullable=False),
        sa.Column("suggested_evidence_ids", postgresql.JSONB(), nullable=False),
        sa.Column("gap_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float()),
        sa.Column("generated_by_llm_call_id", sa.Integer(), sa.ForeignKey("llm_call.id", ondelete="SET NULL")),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("final_body", sa.Text()),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
    )
    op.create_foreign_key(
        "fk_users_engagement_scope", "users", "audit_engagements",
        ["engagement_scope_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_engagement_scope", "users", type_="foreignkey")
    op.drop_table("answer_drafts")
    op.drop_table("audit_questions")
    op.drop_table("audit_engagements")
