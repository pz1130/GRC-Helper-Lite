"""Mark LLM calls whose model is not in the price table.

`pricing.estimate_cost` 对未知模型返回 0（有意为之：成本统计不准可以接受，
流水线挂掉不可接受）。但未计价和"真的零成本"在库里长得一模一样，于是预算卡
照常显示 $0.00 / 预算，`monthly_budget_usd` 那道闸门静默放行。

回填：历史行按当时的价格表判定不可靠（价格表会变），一律留 false——
这条标记只对新调用负责，不假装能重建历史。
"""

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_call",
        sa.Column("cost_unknown", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("llm_call", "cost_unknown")
