"""Add a token version to users so a password change ends existing sessions."""

import sqlalchemy as sa

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    # 已有账号从 0 开始；升级前签发的令牌没有 ver，也按 0 算，所以升级本身不踢人。
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("users", "token_version")
