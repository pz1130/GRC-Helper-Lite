"""Stop a deleted evidence type from taking registered evidence with it.

`evidence_items.evidence_type_id` 原本是 ON DELETE CASCADE。2026-09-19 在生产栈上
清理数据时实测：删掉一个证据类型返回 204，而那条已登记的审计证据被静默删除，
审计日志里只有 evidence_type.delete，**没有任何一条记录那份证据也没了**。
这违反铁律 2（不删行、每次改动都要留审计）。

对照组就在同一个代码库里：`llm_task_routing` 指向 provider 的外键是 RESTRICT，
被引用时后端返 409 并说明还有谁在用它。证据类型照着这个来。

只改这一条。`control_id` 的 CASCADE 暂不动——控制点走的是 status='merged' 而
不是 DELETE（铁律 2），实际删不掉，风险面不一样，要改应当单独评估。
"""

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_FK = "evidence_items_evidence_type_id_fkey"


def _recreate(ondelete: str) -> None:
    op.drop_constraint(_FK, "evidence_items", type_="foreignkey")
    op.create_foreign_key(
        _FK, "evidence_items", "evidence_types", ["evidence_type_id"], ["id"], ondelete=ondelete
    )


def upgrade() -> None:
    _recreate("RESTRICT")


def downgrade() -> None:
    _recreate("CASCADE")


_ = sa
