"""Maturity assessments and dual-dimension scores."""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    op.create_table(
        "maturity_assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "framework_id",
            sa.Integer(),
            sa.ForeignKey("frameworks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column(
            "status", _enum("draft", "final", name="maturity_assessment_status"), nullable=False
        ),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_maturity_assessments_framework_id", "maturity_assessments", ["framework_id"]
    )
    op.create_table(
        "maturity_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "assessment_id",
            sa.Integer(),
            sa.ForeignKey("maturity_assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "framework_item_id",
            sa.Integer(),
            sa.ForeignKey("framework_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("doc_score", sa.Integer(), nullable=False),
        sa.Column("impl_score", sa.Integer(), nullable=False),
        sa.Column("doc_rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("impl_rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "doc_score_source",
            _enum("ai", "human", name="maturity_doc_score_source"),
            nullable=False,
        ),
        sa.Column(
            "impl_score_source",
            _enum("ai", "human", name="maturity_impl_score_source"),
            nullable=False,
        ),
        sa.Column("scored_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "scored_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("assessment_id", "framework_item_id", name="uq_maturity_score_item"),
        sa.CheckConstraint("doc_score BETWEEN 0 AND 4", name="ck_maturity_doc_score"),
        sa.CheckConstraint("impl_score BETWEEN 0 AND 4", name="ck_maturity_impl_score"),
    )
    op.create_index("ix_maturity_scores_assessment_id", "maturity_scores", ["assessment_id"])
    op.create_index(
        "ix_maturity_scores_framework_item_id", "maturity_scores", ["framework_item_id"]
    )


def downgrade() -> None:
    op.drop_table("maturity_scores")
    op.drop_table("maturity_assessments")
