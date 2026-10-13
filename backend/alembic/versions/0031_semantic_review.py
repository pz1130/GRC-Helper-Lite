"""保存规范化结果及独立语义复核，支持失败后分阶段恢复。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("extraction_inventories", sa.Column("normalized_payload", postgresql.JSONB(), nullable=True))
    op.add_column("extraction_inventories", sa.Column("normalization_call_id", sa.Integer(), sa.ForeignKey("llm_call.id"), nullable=True))
    op.add_column("extraction_inventories", sa.Column("semantic_review", postgresql.JSONB(), nullable=True))
    op.add_column("extraction_inventories", sa.Column("review_call_id", sa.Integer(), sa.ForeignKey("llm_call.id"), nullable=True))


def downgrade():
    for name in ("review_call_id", "semantic_review", "normalization_call_id", "normalized_payload"):
        op.drop_column("extraction_inventories", name)
