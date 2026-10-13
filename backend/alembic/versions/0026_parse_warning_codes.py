"""Carry a translatable code next to each parse warning.

`documents.parse_warnings` 存的是写死的中文句子，文档列表与详情页原样显示——
英文界面上就是中文。和业务错误同一个病，治法也一样：带一个 code 和句子里的
变量，前端据此翻译。

与业务错误不同的是告警**存在库里**。已经在库里的行没有 code，也没法从句子
反推——所以这里只加一个可空列，不回填。老行是 NULL，前端回落到
`parse_warnings` 那句中文，显示效果与迁移前完全一致。

句子本身**留着不删**：它是前端还没有某条词条时的兜底，也是唯一能看懂老行的东西。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("parse_warning_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "parse_warning_codes")
