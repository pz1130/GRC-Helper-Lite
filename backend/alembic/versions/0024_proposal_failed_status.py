"""Add the 'failed' proposal status: a batch that produced nothing.

抽取按批跑，一批模型输出不合 schema 时原先只把 rejected 计数 +1 然后跳过：
错误落进 llm_call.error，worker 日志一条不打，界面只说"任务已入队"。
生产栈实测一份 37 条款的文档切 7 批、2 批静默丢掉——对一个以"不漏控制点"
为立身之本的工具，这是正确性问题。

status 用的是非原生枚举（native_enum=False）+ CHECK 约束，所以加值要重建约束。
"""

import sqlalchemy as sa

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_OLD = ("pending", "accepted", "modified", "rejected")
_NEW = (*_OLD, "failed")
_CONSTRAINT = "proposal_status"


def _recreate(values: tuple[str, ...]) -> None:
    op.drop_constraint(_CONSTRAINT, "proposals", type_="check")
    op.create_check_constraint(
        _CONSTRAINT, "proposals", sa.column("status").in_(values)
    )


def upgrade() -> None:
    _recreate(_NEW)


def downgrade() -> None:
    # 先把 failed 行降级成 rejected，否则新约束建不起来。
    op.execute(
        "UPDATE proposals SET status = 'rejected', "
        "reject_reason = coalesce(reject_reason, '') || ' [该批次抽取失败]' "
        "WHERE status = 'failed'"
    )
    _recreate(_OLD)
