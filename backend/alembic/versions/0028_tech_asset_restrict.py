"""Stop a deleted tech asset from silently unlinking implementations and evidence.

`implementations.tech_asset_id` 与 `evidence_items.tech_asset_id` 原本是
ON DELETE SET NULL：删掉一个技术资产返回 204，引用它的控制点实现与证据被数据库
悄悄清掉了资产——「这个控制点靠 VaultKeeper 落地」就这么没了，准备度里的工具名
也跟着消失，审计日志里只有 tech_asset.delete。与 0025 修证据类型是同一个病，
治法也一样：改成 RESTRICT，后端先查引用并说明还有谁在用它。
"""

import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_FKS = (
    ("implementations_tech_asset_id_fkey", "implementations"),
    ("evidence_items_tech_asset_id_fkey", "evidence_items"),
)


def _recreate(ondelete: str) -> None:
    for name, table in _FKS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, "tech_assets", ["tech_asset_id"], ["id"], ondelete=ondelete)


def upgrade() -> None:
    _recreate("RESTRICT")


def downgrade() -> None:
    _recreate("SET NULL")


_ = sa
