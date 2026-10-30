"""指标口径、不可变观测，以及自动事件的实施范围。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def _protect_target(extra: str) -> None:
    op.execute(f"""
        CREATE OR REPLACE FUNCTION rcsa_protect_implementation_target() RETURNS trigger AS $$
        BEGIN
            IF NEW.tech_asset_id IS DISTINCT FROM OLD.tech_asset_id AND (
               EXISTS (SELECT 1 FROM rcsa_items WHERE implementation_id = OLD.id) {extra}) THEN
                RAISE EXCEPTION 'Implementation is referenced by RCSA' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def upgrade() -> None:
    op.create_table("rcsa_kris",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("unit", sa.String(50), nullable=False),
        sa.Column("control_id", sa.Integer(), sa.ForeignKey("controls.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("implementation_id", sa.Integer()),
        sa.Column("target_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("direction", sa.String(4), nullable=False),
        sa.Column("amber_threshold", sa.Numeric(20, 6), nullable=False),
        sa.Column("red_threshold", sa.Numeric(20, 6), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("decision_days", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["implementation_id", "control_id"], ["implementations.id", "implementations.control_id"],
                                name="fk_rcsa_kri_implementation", ondelete="RESTRICT"),
        sa.CheckConstraint("direction IN ('high', 'low')", name="ck_rcsa_kri_direction"),
        sa.CheckConstraint("(direction = 'high' AND amber_threshold < red_threshold) OR "
                           "(direction = 'low' AND amber_threshold > red_threshold)", name="ck_rcsa_kri_thresholds"),
        sa.CheckConstraint("decision_days BETWEEN 1 AND 90", name="ck_rcsa_kri_decision_days"),
    )
    op.add_column("rcsa_triggers", sa.Column("origin_kri_id", sa.Integer()))
    op.create_foreign_key("rcsa_triggers_origin_kri_id_fkey", "rcsa_triggers", "rcsa_kris",
                          ["origin_kri_id"], ["id"], ondelete="RESTRICT")
    op.drop_constraint("rcsa_trigger_type", "rcsa_triggers", type_="check")
    op.create_check_constraint("rcsa_trigger_type", "rcsa_triggers", "trigger_type IN ('planned_change', 'incident', 'other', 'kri')")
    op.add_column("rcsa_trigger_controls", sa.Column("implementation_id", sa.Integer()))
    op.add_column("rcsa_trigger_controls", sa.Column("implementation_snapshot", postgresql.JSONB()))
    op.create_foreign_key("fk_rcsa_trigger_implementation", "rcsa_trigger_controls", "implementations",
                          ["implementation_id", "control_id"], ["id", "control_id"], ondelete="RESTRICT")
    op.create_table("rcsa_kri_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kri_id", sa.Integer(), sa.ForeignKey("rcsa_kris.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Numeric(20, 6), nullable=False),
        sa.Column("source_ref", sa.String(500), nullable=False),
        sa.Column("band", sa.String(5), nullable=False),
        sa.Column("rule_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("trigger_id", sa.Integer(), sa.ForeignKey("rcsa_triggers.id", ondelete="RESTRICT")),
        sa.Column("received_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("kri_id", "external_id", name="uq_rcsa_kri_observation_external"),
        sa.CheckConstraint("band IN ('green', 'amber', 'red')", name="ck_rcsa_kri_observation_band"),
    )
    op.create_index("ix_rcsa_kri_observation_time", "rcsa_kri_observations", ["kri_id", "observed_at"])
    _protect_target("OR EXISTS (SELECT 1 FROM rcsa_kris WHERE implementation_id = OLD.id) "
                    "OR EXISTS (SELECT 1 FROM rcsa_trigger_controls WHERE implementation_id = OLD.id)")
    op.execute("""
        CREATE FUNCTION rcsa_kri_observations_freeze() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'KRI observations are append only' USING ERRCODE = '23000';
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("CREATE TRIGGER rcsa_kri_observations_freeze BEFORE UPDATE OR DELETE ON rcsa_kri_observations "
               "FOR EACH ROW EXECUTE FUNCTION rcsa_kri_observations_freeze()")
    op.execute("""
        CREATE FUNCTION rcsa_kri_definition_freeze() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Disable KRI definitions instead of deleting' USING ERRCODE = '23000';
            END IF;
            IF (to_jsonb(NEW) - ARRAY['enabled','owner_id','version','updated_by','updated_at']) IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['enabled','owner_id','version','updated_by','updated_at']) THEN
                RAISE EXCEPTION 'Create a new KRI for a changed definition' USING ERRCODE = '23000';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    op.execute("CREATE TRIGGER rcsa_kri_definition_freeze BEFORE UPDATE OR DELETE ON rcsa_kris "
               "FOR EACH ROW EXECUTE FUNCTION rcsa_kri_definition_freeze()")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM rcsa_kris) OR EXISTS "
                                   "(SELECT 1 FROM rcsa_trigger_controls WHERE implementation_id IS NOT NULL)")):
        raise RuntimeError("KRI or implementation-scoped trigger data exists; fix forward instead")
    op.execute("DROP TRIGGER rcsa_kri_definition_freeze ON rcsa_kris")
    op.execute("DROP FUNCTION rcsa_kri_definition_freeze()")
    op.execute("DROP TRIGGER rcsa_kri_observations_freeze ON rcsa_kri_observations")
    op.execute("DROP FUNCTION rcsa_kri_observations_freeze()")
    _protect_target("")
    op.drop_table("rcsa_kri_observations")
    op.drop_constraint("fk_rcsa_trigger_implementation", "rcsa_trigger_controls", type_="foreignkey")
    op.drop_column("rcsa_trigger_controls", "implementation_snapshot")
    op.drop_column("rcsa_trigger_controls", "implementation_id")
    op.drop_constraint("rcsa_triggers_origin_kri_id_fkey", "rcsa_triggers", type_="foreignkey")
    op.drop_column("rcsa_triggers", "origin_kri_id")
    op.drop_constraint("rcsa_trigger_type", "rcsa_triggers", type_="check")
    op.create_check_constraint("rcsa_trigger_type", "rcsa_triggers", "trigger_type IN ('planned_change', 'incident', 'other')")
    op.drop_table("rcsa_kris")
