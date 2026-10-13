"""Framework layer: frameworks, items and control mappings."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "frameworks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False, unique=True),
        sa.Column("name_zh", sa.String(200), nullable=False),
        sa.Column("name_en", sa.String(200), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("source", sa.String(500), nullable=False, server_default=""),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("imported_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "framework_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("framework_id", sa.Integer(), sa.ForeignKey("frameworks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("framework_items.id", ondelete="CASCADE")),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attributes", postgresql.JSONB()),
        sa.UniqueConstraint("framework_id", "code", name="uq_framework_item_code"),
    )
    op.create_index("ix_framework_items_framework_id", "framework_items", ["framework_id"])
    op.create_index("ix_framework_items_parent_id", "framework_items", ["parent_id"])
    op.create_index("ix_framework_item_attributes", "framework_items", ["attributes"], postgresql_using="gin")
    op.create_table(
        "mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("control_id", sa.Integer(), sa.ForeignKey("controls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("framework_item_id", sa.Integer(), sa.ForeignKey("framework_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "strength",
            sa.Enum("full", "partial", "supporting", name="mapping_strength", native_enum=False,
                    create_constraint=True, length=32),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("quote", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float()),
        sa.Column("proposed_by_llm_call_id", sa.Integer(), sa.ForeignKey("llm_call.id", ondelete="SET NULL")),
        sa.Column("confirmed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("control_id", "framework_item_id", name="uq_control_framework_item"),
    )
    op.create_index("ix_mappings_control_id", "mappings", ["control_id"])
    op.create_index("ix_mappings_framework_item_id", "mappings", ["framework_item_id"])


def downgrade() -> None:
    op.drop_table("mappings")
    op.drop_table("framework_items")
    op.drop_table("frameworks")
