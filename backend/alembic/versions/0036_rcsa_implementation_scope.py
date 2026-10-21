"""RCSA 按控制实施记录划分评估范围，旧评估保留整项控制口径。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rcsa_items", sa.Column("implementation_id", sa.Integer(), nullable=True))
    op.add_column("rcsa_items", sa.Column("implementation_snapshot", postgresql.JSONB(), nullable=True))
    op.create_unique_constraint("uq_implementation_id_control", "implementations", ["id", "control_id"])
    op.create_foreign_key("fk_rcsa_item_implementation", "rcsa_items", "implementations",
                          ["implementation_id", "control_id"], ["id", "control_id"], ondelete="RESTRICT")
    op.drop_constraint("uq_rcsa_item_control", "rcsa_items", type_="unique")
    op.create_index("uq_rcsa_item_control", "rcsa_items", ["campaign_id", "control_id"], unique=True,
                    postgresql_where=sa.text("implementation_id IS NULL"))
    op.create_unique_constraint("uq_rcsa_item_implementation", "rcsa_items", ["campaign_id", "implementation_id"])
    op.execute("""
        CREATE FUNCTION rcsa_protect_implementation_target() RETURNS trigger AS $$
        BEGIN
            IF NEW.tech_asset_id IS DISTINCT FROM OLD.tech_asset_id AND
               EXISTS (SELECT 1 FROM rcsa_items WHERE implementation_id = OLD.id) THEN
                RAISE EXCEPTION 'Implementation is referenced by RCSA' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER rcsa_protect_implementation_target BEFORE UPDATE ON implementations
        FOR EACH ROW EXECUTE FUNCTION rcsa_protect_implementation_target();
    """)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM rcsa_items WHERE implementation_id IS NOT NULL)")):
        raise RuntimeError("Implementation-scoped RCSA items exist; fix forward instead")
    op.execute("DROP TRIGGER rcsa_protect_implementation_target ON implementations")
    op.execute("DROP FUNCTION rcsa_protect_implementation_target()")
    op.drop_constraint("uq_rcsa_item_implementation", "rcsa_items", type_="unique")
    op.drop_index("uq_rcsa_item_control", table_name="rcsa_items")
    op.create_unique_constraint("uq_rcsa_item_control", "rcsa_items", ["campaign_id", "control_id"])
    op.drop_constraint("fk_rcsa_item_implementation", "rcsa_items", type_="foreignkey")
    op.drop_constraint("uq_implementation_id_control", "implementations", type_="unique")
    op.drop_column("rcsa_items", "implementation_snapshot")
    op.drop_column("rcsa_items", "implementation_id")
