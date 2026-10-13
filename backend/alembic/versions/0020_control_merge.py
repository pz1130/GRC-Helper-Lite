"""Control merge destination."""

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("controls", sa.Column("merged_into_id", sa.Integer(), nullable=True))
    op.create_index("ix_controls_merged_into_id", "controls", ["merged_into_id"])
    op.create_foreign_key(
        "fk_controls_merged_into_id",
        "controls",
        "controls",
        ["merged_into_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_controls_merged_into_id", "controls", type_="foreignkey")
    op.drop_index("ix_controls_merged_into_id", table_name="controls")
    op.drop_column("controls", "merged_into_id")
