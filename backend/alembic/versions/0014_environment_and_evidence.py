"""Environment registry and evidence library."""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    op.create_table(
        "tech_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column(
            "category",
            _enum(
                "cloud", "pam", "siem", "edr", "iam", "dlp", "backup", "network",
                "database", "ticketing", "other", name="tech_asset_category"
            ),
            nullable=False,
        ),
        sa.Column("vendor", sa.String(200), nullable=False, server_default=""),
        sa.Column(
            "environment",
            _enum("prod", "dr", "dev", "all", name="tech_asset_environment"),
            nullable=False,
        ),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("scope_note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status",
            _enum("active", "planned", "retiring", name="tech_asset_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_tech_assets_owner_user_id", "tech_assets", ["owner_user_id"])

    op.create_table(
        "implementations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "control_id", sa.Integer(), sa.ForeignKey("controls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tech_asset_id", sa.Integer(), sa.ForeignKey("tech_assets.id", ondelete="SET NULL"),
        ),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "how_enforced",
            _enum("automated", "semi_automated", "manual", name="implementation_how_enforced"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "implemented", "partial", "planned", "not_applicable", name="implementation_status"
            ),
            nullable=False,
        ),
        sa.Column("na_justification", sa.Text()),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("last_verified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("control_id", "tech_asset_id", name="uq_implementation_control_asset"),
        sa.CheckConstraint(
            "status <> 'not_applicable' OR "
            "(na_justification IS NOT NULL AND btrim(na_justification) <> '')",
            name="ck_implementation_na_justification",
        ),
    )
    op.create_index("ix_implementations_control_id", "implementations", ["control_id"])
    op.create_index("ix_implementations_tech_asset_id", "implementations", ["tech_asset_id"])
    op.create_index("ix_implementations_owner_user_id", "implementations", ["owner_user_id"])
    op.create_index(
        "uq_implementation_control_without_asset",
        "implementations",
        ["control_id"],
        unique=True,
        postgresql_where=sa.text("tech_asset_id IS NULL"),
    )

    op.create_table(
        "evidence_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name_zh", sa.String(200), nullable=False),
        sa.Column("name_en", sa.String(200), nullable=False),
        sa.Column("format", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "cadence",
            _enum(
                "monthly", "quarterly", "semiannual", "annual", "ad_hoc", name="evidence_cadence"
            ),
            nullable=False,
        ),
        sa.Column("typical_source", sa.String(500), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "evidence_type_id",
            sa.Integer(),
            sa.ForeignKey("evidence_types.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "control_id", sa.Integer(), sa.ForeignKey("controls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tech_asset_id", sa.Integer(), sa.ForeignKey("tech_assets.id", ondelete="SET NULL"),
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("location_hint", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_collected_at", sa.DateTime(timezone=True)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("file_path", sa.String(1000)),
        sa.Column(
            "status",
            _enum("planned", "collected", "missing", name="evidence_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status <> 'collected' OR last_collected_at IS NOT NULL",
            name="ck_evidence_collected_at",
        ),
    )
    op.create_index("ix_evidence_items_evidence_type_id", "evidence_items", ["evidence_type_id"])
    op.create_index("ix_evidence_items_control_id", "evidence_items", ["control_id"])
    op.create_index("ix_evidence_items_tech_asset_id", "evidence_items", ["tech_asset_id"])
    op.create_index("ix_evidence_items_owner_user_id", "evidence_items", ["owner_user_id"])


def downgrade() -> None:
    op.drop_table("evidence_items")
    op.drop_table("evidence_types")
    op.drop_table("implementations")
    op.drop_table("tech_assets")
