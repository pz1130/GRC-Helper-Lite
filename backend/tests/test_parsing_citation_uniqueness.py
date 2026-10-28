"""撞号复现探索测试（citation_label 文档级唯一性缺陷）。

本模块对应 bugfix 流程的任务 1：用合成 ClauseNode 树驱动 flatten()，
断言同一文档内所有 citation_label 互不相同。

缺陷：flatten() 没有文档级唯一性检查，build_citation_label 直接返回裸编号，
于是不同（重启）编号作用域里的两个条款会得到相同的 citation_label。

**这些测试在未修复代码上必须 FAIL**——失败即证明缺陷存在。
解析层不触数据库，用合成树驱动 flatten(nodes, document_id=1) 即可，无需 DB fixture。
"""

from app.parsing.contract import ClauseNode
from app.parsing.flatten import flatten


def _all_labels_unique(rows: list[dict]) -> bool:
    """期望行为：文档内每行的 citation_label 唯一。

    对齐 design 的 isBugCondition(rows)：存在被多行共享的 citation_label 即撞号。
    """
    return len({r["citation_label"] for r in rows}) == len(rows)


def test_正文与附录1撞号时引用标签应唯一():
    """用例 1「正文与附录 1 撞号」。

    正文顶层编号条款 number="1"，外加一个未编号附录标题（number=None）
    其下子条款 number="1"。未修复代码下两行 citation_label 均为 "1"，
    唯一性断言 FAIL。
    """
    nodes = [
        ClauseNode(heading="总则", text="正文第一条。", level=1, number="1"),
        ClauseNode(
            heading="附录 A",
            text="",
            level=1,
            number=None,
            children=[
                ClauseNode(heading="附录第一项", text="附录第一条。", level=2, number="1"),
            ],
        ),
    ]

    rows = flatten(nodes, document_id=1)

    labels = [r["citation_label"] for r in rows]
    assert _all_labels_unique(rows), f"citation_label 相撞：{labels}"


def test_无未编号祖先的撞号时引用标签应唯一():
    """用例 2「无未编号祖先的撞号」。

    两个顶层编号条款都是 number="1"，且没有任何未编号祖先。
    未修复代码下两行 citation_label 均为 "1"，唯一性断言 FAIL。
    """
    nodes = [
        ClauseNode(heading="第一章", text="第一章正文。", level=1, number="1"),
        ClauseNode(heading="另一章", text="另一章正文。", level=1, number="1"),
    ]

    rows = flatten(nodes, document_id=1)

    labels = [r["citation_label"] for r in rows]
    assert _all_labels_unique(rows), f"citation_label 相撞：{labels}"


def test_两个同名附录各含1时引用标签应唯一():
    """用例 3「两个同名附录各含 1」。

    两个未编号标题都叫 "附录"，各含一个子条款 number="1"。
    未修复代码下两个子条款 citation_label 均为 "1"，唯一性断言 FAIL。
    注：即便后续仅用最近未编号祖先标题限定，也会得到两个 "附录 › 1"，
    因此本用例还暴露了需要确定性最终兜底的场景。
    """
    nodes = [
        ClauseNode(
            heading="附录",
            text="",
            level=1,
            number=None,
            children=[
                ClauseNode(heading="第一项", text="第一个附录的第一条。", level=2, number="1"),
            ],
        ),
        ClauseNode(
            heading="附录",
            text="",
            level=1,
            number=None,
            children=[
                ClauseNode(heading="第一项", text="第二个附录的第一条。", level=2, number="1"),
            ],
        ),
    ]

    rows = flatten(nodes, document_id=1)

    labels = [r["citation_label"] for r in rows]
    assert _all_labels_unique(rows), f"citation_label 相撞：{labels}"
