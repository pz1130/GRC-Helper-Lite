"""document and clause tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-08
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column(
            "doc_type",
            sa.Enum(
                "policy",
                "standard",
                "procedure",
                "guideline",
                name="doc_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "uploaded",
                "parsing",
                "active",
                "superseded",
                "parse_failed",
                name="doc_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("owner", sa.String(length=255), nullable=True),
        sa.Column("approver", sa.String(length=255), nullable=True),
        sa.Column("approved_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("review_due_date", sa.Date(), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=False),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("parse_warnings", sa.Text(), nullable=True),
        sa.Column("ocr_quality_flag", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(["supersedes_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_hash"),
    )
    op.create_table(
        "clauses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("number", sa.String(length=64), nullable=True),
        sa.Column("heading", sa.String(length=1000), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=False),
        sa.Column("citation_label", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("page_ref", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
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
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["clauses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_clauses_document_id"), "clauses", ["document_id"], unique=False)
    op.create_index(
        "ix_clauses_document_order", "clauses", ["document_id", "order_index"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_clauses_document_order", table_name="clauses")
    op.drop_index(op.f("ix_clauses_document_id"), table_name="clauses")
    op.drop_table("clauses")
    op.drop_table("documents")
