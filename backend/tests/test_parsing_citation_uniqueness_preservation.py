"""保全性测试（citation_label 文档级唯一性缺陷）——观察优先。

本模块对应 bugfix 流程的任务 2：锁定"不撞号的输入完全不受修复影响"的基线行为。
对齐 design 的 NOT isBugCondition(rows) 范围，覆盖 Requirements 3.1–3.4。

**观察优先（observation-first）**：下面的 EXPECTED_BASELINE_ROWS 是在 **未修复** 代码上
真实运行 flatten(NO_COLLISION_NODES, document_id=1) 捕获的完整返回（含 _parent_key /
_source_keys 等所有键）。这些断言在未修复代码上 **必须 PASS**；修复后它们仍须逐字节
通过——因为无撞号时第二趟后处理应为 no-op。

解析层不触数据库，用合成 ClauseNode 树驱动 flatten(nodes, document_id=1) 即可，无需 DB fixture。
"""

from app.parsing.contract import ClauseNode
from app.parsing.flatten import flatten

# 无撞号合成树：
# - 根 1「引言」未编号（number=None），heading_path="引言"，文档内唯一 → 覆盖 3.2
# - 根 2「总则」唯一编号 number="3.4.2"，带子条款「定义」number="3.4.2.1" → 覆盖 3.1
# 三行标签分别为 "引言" / "3.4.2" / "3.4.2.1"，互不相同（无任何撞号）。
NO_COLLISION_NODES = [
    ClauseNode(heading="引言", text="引言正文。", level=1, number=None),
    ClauseNode(
        heading="总则",
        text="总则正文。",
        level=1,
        number="3.4.2",
        page_ref=7,
        source_keys=["blk-1", "blk-2"],
        children=[
            ClauseNode(
                heading="定义",
                text="定义正文。",
                level=2,
                number="3.4.2.1",
                kind="table",
                page_ref=8,
                source_keys=["blk-3"],
            ),
        ],
    ),
]

# 在未修复代码上观察到的完整返回（逐字节基线快照，含所有键）。
# 修复后 flatten(NO_COLLISION_NODES, document_id=1) 必须深度等于此常量。
EXPECTED_BASELINE_ROWS = [
    {
        "_parent_key": None,
        "_source_keys": [],
        "document_id": 1,
        "number": None,
        "heading": "引言",
        "heading_path": "引言",
        "citation_label": "引言",
        "text": "引言正文。",
        "order_index": 0,
        "level": 1,
        "page_ref": None,
        "kind": "section",
        "language": "en",
    },
    {
        "_parent_key": None,
        "_source_keys": ["blk-1", "blk-2"],
        "document_id": 1,
        "number": "3.4.2",
        "heading": "总则",
        "heading_path": "总则",
        "citation_label": "3.4.2",
        "text": "总则正文。",
        "order_index": 1,
        "level": 1,
        "page_ref": 7,
        "kind": "section",
        "language": "en",
    },
    {
        "_parent_key": 1,
        "_source_keys": ["blk-3"],
        "document_id": 1,
        "number": "3.4.2.1",
        "heading": "定义",
        "heading_path": "总则 › 定义",
        "citation_label": "3.4.2.1",
        "text": "定义正文。",
        "order_index": 2,
        "level": 2,
        "page_ref": 8,
        "kind": "table",
        "language": "en",
    },
]

# 修复后只允许"撞号行的 citation_label"改变；其余字段逐行逐键保持不变。
_PRESERVED_KEYS = [
    "number",
    "heading",
    "heading_path",
    "order_index",
    "level",
    "page_ref",
    "kind",
    "language",
    "_parent_key",
    "_source_keys",
]


def test_无撞号文档逐字节一致():
    """用例 1「无撞号文档逐字节一致」（Requirement 3.3）。

    无任何撞号时，flatten() 的完整返回必须逐字节等于锁定基线——
    第二趟后处理应为 no-op。这是本次修复最关键的回归护栏。
    """
    rows = flatten(NO_COLLISION_NODES, document_id=1)
    assert rows == EXPECTED_BASELINE_ROWS


def test_唯一编号保持裸标签():
    """用例 2「唯一编号保持裸标签」（Requirement 3.1）。

    文档内唯一的带编号条款（number="3.4.2"）其 citation_label 必须是裸编号，
    不得被任何作用域限定。
    """
    rows = flatten(NO_COLLISION_NODES, document_id=1)
    总则行 = next(r for r in rows if r["heading"] == "总则")
    assert 总则行["number"] == "3.4.2"
    assert 总则行["citation_label"] == "3.4.2"


def test_未编号非撞号保持heading_path():
    """用例 3「未编号非撞号保持 heading_path」（Requirement 3.2）。

    未编号（number=None）且 heading_path 文档内唯一的条款，
    其 citation_label 必须回退为 heading_path 本身（这里是 "引言"）。
    """
    rows = flatten(NO_COLLISION_NODES, document_id=1)
    引言行 = next(r for r in rows if r["heading"] == "引言")
    assert 引言行["number"] is None
    assert 引言行["citation_label"] == 引言行["heading_path"] == "引言"


def test_其他字段不变():
    """用例 4「其他字段不变」（Requirement 3.4）。

    对无撞号树，逐行断言 number/heading/heading_path/order_index/level/
    page_ref/kind/language/_parent_key/_source_keys 与基线逐键相等——
    只有相撞行的 citation_label 允许改变，而此树无任何撞号。
    """
    rows = flatten(NO_COLLISION_NODES, document_id=1)
    assert len(rows) == len(EXPECTED_BASELINE_ROWS)
    for 实际行, 基线行 in zip(rows, EXPECTED_BASELINE_ROWS, strict=True):
        for key in _PRESERVED_KEYS:
            assert 实际行[key] == 基线行[key], (
                f"行 order_index={基线行['order_index']} 的字段 {key} 发生变化："
                f"{实际行[key]!r} != {基线行[key]!r}"
            )
