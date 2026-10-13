"""Carry a translatable code next to the parse failure reason.

`documents.parse_error` 存的是写死的中文句子，文档列表与详情页原样显示。
2026-09-23 起产品只有英文界面，这句中文所有用户都会看到。治法与 0026 的解析
告警相同：另存 code 和句子里的变量，前端按 code 查词条。

已经在库里的失败行没有 code，也没法从句子反推，所以只加可空列、不回填：
老行是 NULL，前端回落到 `parse_error` 那句原文，显示与迁移前一致。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("parse_error_code", sa.String(64), nullable=True))
    op.add_column(
        "documents",
        sa.Column("parse_error_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "parse_error_params")
    op.drop_column("documents", "parse_error_code")
