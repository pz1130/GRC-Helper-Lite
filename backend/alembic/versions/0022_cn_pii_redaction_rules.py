"""Seed redaction rules for mainland-China PII: mobile numbers and ID cards.

默认规则集原本只有 IPv4 和邮箱两条（0004）。制度正文、工单摘录和审计答复里
出现个人手机号、身份证号是常态，这两类原样发给第三方模型就是一次 PII 外泄。

两个 ruleset 都种：embedding 那套虽然"宽松"（保留业务术语以免削弱检索），
但 PII 不是业务术语，脱掉不影响向量质量。

身份证的正则带日期段校验（年 19xx/20xx、月 01-12、日 01-31），不是简单的
`\\d{17}[\\dXx]`。脱敏的假阳性代价不对称：被替换掉的内容模型再也看不见，
把 19 位卡号或流水号打成 [[IDCARD_1]] 会让抽取出来的控制点丢掉关键事实。

**不要用裸 SQL 种这两条。** 走过两个坑：身份证正则里的 `(?:19|20)` 含 `:19`，
拼进 SQL 文本会被当成命名绑定参数；改成绑定参数后，同一个参数既出现在 SELECT
列表又出现在 WHERE 里比对 varchar 列，asyncpg 报 "inconsistent types deduced"。
两次都是迁移照常打印 "Running upgrade 0021 -> 0022" 而一行没插，查库才发现——
**种子迁移跑完必须回查一次行数**，只看 alembic 的输出不算数。
"""

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

# order_index 接在既有的 IP(10) / EMAIL(20) 之后。
_RULES = (
    ("PHONE", r"\b1[3-9]\d{9}\b", 30, "中国大陆手机号"),
    (
        "IDCARD",
        r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",
        40,
        "中国大陆居民身份证号（18 位）",
    ),
)

# 幂等：已经手工在界面上加过同类规则的库，不要种出第二条。
_TABLE = sa.table(
    "llm_redaction_rule",
    sa.column("ruleset", sa.String),
    sa.column("pattern_type", sa.String),
    sa.column("pattern", sa.Text),
    sa.column("replacement_prefix", sa.String),
    sa.column("enabled", sa.Boolean),
    sa.column("order_index", sa.Integer),
    sa.column("note", sa.Text),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(
        bind.execute(
            sa.text("SELECT ruleset, replacement_prefix FROM llm_redaction_rule")
        ).all()
    )
    rows = [
        {
            "ruleset": ruleset,
            "pattern_type": "regex",
            "pattern": pattern,
            "replacement_prefix": prefix,
            "enabled": True,
            "order_index": order_index,
            "note": note,
        }
        for ruleset in ("generation", "embedding")
        for prefix, pattern, order_index, note in _RULES
        if (ruleset, prefix) not in existing
    ]
    if rows:
        op.bulk_insert(_TABLE, rows)


def downgrade() -> None:
    op.execute(
        "DELETE FROM llm_redaction_rule WHERE replacement_prefix IN ('PHONE', 'IDCARD')"
    )
