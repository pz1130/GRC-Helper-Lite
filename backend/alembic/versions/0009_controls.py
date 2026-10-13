"""Control library and its provenance links."""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "controls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("category", sa.String(100)),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "control_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "control_id",
            sa.Integer(),
            sa.ForeignKey("controls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "clause_id",
            sa.Integer(),
            sa.ForeignKey("clauses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "relation",
            sa.Enum(
                "defines",
                "elaborates",
                "implements",
                name="source_relation",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float()),
        sa.Column(
            "proposed_by_llm_call_id",
            sa.Integer(),
            sa.ForeignKey("llm_call.id", ondelete="SET NULL"),
        ),
        sa.Column("confirmed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("control_id", "clause_id", name="uq_control_clause"),
    )
    op.create_index("ix_control_sources_control_id", "control_sources", ["control_id"])
    op.create_index("ix_control_sources_clause_id", "control_sources", ["clause_id"])
    op.create_table(
        "control_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "from_control_id",
            sa.Integer(),
            sa.ForeignKey("controls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_control_id",
            sa.Integer(),
            sa.ForeignKey("controls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "relation_type",
            sa.Enum(
                "implements",
                "refines",
                "depends_on",
                "conflicts_with",
                "duplicates",
                name="control_relation_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("confirmed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "from_control_id", "to_control_id", "relation_type", name="uq_control_relation"
        ),
    )
    op.create_index(
        "ix_control_relations_from_control_id", "control_relations", ["from_control_id"]
    )
    op.create_index("ix_control_relations_to_control_id", "control_relations", ["to_control_id"])


def downgrade() -> None:
    op.drop_table("control_relations")
    op.drop_table("control_sources")
    op.drop_table("controls")
