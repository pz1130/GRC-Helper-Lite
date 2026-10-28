"""补充单元测试与确定性穷举式属性测试（citation_label 文档级唯一性修复）。

本模块对应 bugfix 流程的任务 4：在修复已落地的前提下补充两类测试。

1. 单元测试：断言修复后产出的 **具体限定标签**（而不仅是唯一性），
   覆盖 design「Unit Tests」里的四类场景——
   最近未编号祖先限定、完整 heading_path 兜底、order_index 最终兜底、>2 路撞号。

2. 属性测试（PBT）：hypothesis 未安装、也不引入；改用 **确定性穷举/组合生成器**
   构造一批小型合成 ClauseNode 树（变化根数量、编号/未编号、重启编号、
   嵌套附录、重复标题），在循环中逐一验证三条不变式——
   唯一性、确定性、保全性，外加 order_index 最终兜底。

解析层不触数据库，用合成树驱动 flatten(nodes, document_id=1) 即可，无需 DB fixture。
与任务 1/2 的测试不重复：任务 1 只断言唯一性（撞号复现），任务 2 锁定无撞号基线；
本模块断言的是"修复后撞号行被限定成什么具体样子"及"任意树上的不变式"。
"""

from app.parsing.contract import ClauseNode
from app.parsing.flatten import PATH_SEPARATOR, build_citation_label, flatten

# ---------------------------------------------------------------------------
# 单元测试：断言修复后产出的具体限定标签
# ---------------------------------------------------------------------------


def test_正文1与未编号附录下1被限定为具体标签():
    """正文 "1" 保持裸编号，未编号附录「附录 A」下的 "1" 被限定为「附录 A › 1」。

    这是 design 第一级限定（最近未编号祖先标题）的典型场景：
    正文顶层条款无未编号祖先、不被触碰；附录子条款的最近未编号祖先是
    附录标题「附录 A」，故限定为「附录 A › 1」。
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

    assert labels == ["1", "附录 A", "附录 A › 1"]
    assert len(set(labels)) == len(labels)


def test_无未编号祖先的撞号走heading_path兜底():
    """两个顶层编号条款都是 "1" 且无任何未编号祖先 → 用完整 heading_path 限定。

    design 第一级限定无从下手（无未编号祖先），落到第二级兜底 (a)：
    对编号行用 f"{heading_path} › {number}"，得到「第一章 › 1」「另一章 › 1」。
    """
    nodes = [
        ClauseNode(heading="第一章", text="第一章正文。", level=1, number="1"),
        ClauseNode(heading="另一章", text="另一章正文。", level=1, number="1"),
    ]

    rows = flatten(nodes, document_id=1)
    labels = [r["citation_label"] for r in rows]

    assert labels == ["第一章 › 1", "另一章 › 1"]
    assert len(set(labels)) == len(labels)


def test_两个同名附录各含1走order_index最终兜底():
    """两个同名「附录」各含 "1" → 祖先标题与完整路径都不足以区分，落到 order_index。

    - 第一级（最近未编号祖先）后两行均为「附录 › 1」，仍撞号。
    - 第二级完整 heading_path 后两行均为「附录 › 第一项 › 1」，仍撞号
      （标题与路径都相同）。
    - 第三级追加唯一下标 #{order_index}，四行最终全部唯一。
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

    assert labels == [
        "附录 › #0",
        "附录 › 第一项 › 1 › #1",
        "附录 › #2",
        "附录 › 第一项 › 1 › #3",
    ]
    assert len(set(labels)) == len(labels)
    # 最终兜底标记确实出现：这是 order_index 阶梯被触发的证据。
    assert all(f"{PATH_SEPARATOR}#" in label for label in labels)


def test_三路撞号全部被唯一化():
    """>2 路撞号：三个无未编号祖先的顶层 "1" 都应被限定且互不相同。

    三行都无未编号祖先，第一级跳过；第二级用各自完整 heading_path 限定，
    三个标题互异故一步到位区分。
    """
    nodes = [
        ClauseNode(heading="甲章", text="甲章正文。", level=1, number="1"),
        ClauseNode(heading="乙章", text="乙章正文。", level=1, number="1"),
        ClauseNode(heading="丙章", text="丙章正文。", level=1, number="1"),
    ]

    rows = flatten(nodes, document_id=1)
    labels = [r["citation_label"] for r in rows]

    assert labels == ["甲章 › 1", "乙章 › 1", "丙章 › 1"]
    assert len(set(labels)) == len(labels)


# ---------------------------------------------------------------------------
# 确定性穷举式 PBT：合成树库（combinatorial tree bank）
# ---------------------------------------------------------------------------


def _first_pass_labels(nodes: list[ClauseNode]) -> list[str]:
    """复现 flatten 第一趟标签（修复前的裸编号 / heading_path），用于保全性判定。

    与 flatten.walk 的路径拼接、build_citation_label 规则一致，但不做第二趟消歧，
    因此等价于"未修复 flatten"的 citation_label 序列。
    """
    labels: list[str] = []

    def walk(node: ClauseNode, ancestors: list[str]) -> None:
        path = [*ancestors, node.heading]
        heading_path = PATH_SEPARATOR.join(path)
        labels.append(build_citation_label(node.number, heading_path))
        for child in node.children:
            walk(child, path)

    for root in nodes:
        walk(root, [])
    return labels


def _build_tree_bank() -> list[tuple[str, list[ClauseNode]]]:
    """确定性地枚举一批小型合成树，覆盖结构参数的组合。

    变化维度：根数量、编号 vs 未编号、重启编号、嵌套附录、重复标题。
    返回 (名称, 树) 列表，名称仅用于断言失败时定位。
    """
    bank: list[tuple[str, list[ClauseNode]]] = []

    def numbered(heading: str, number: str, children=None) -> ClauseNode:
        return ClauseNode(
            heading=heading, text=f"{heading}正文。", level=1, number=number,
            children=children or [],
        )

    def unnumbered(heading: str, children=None) -> ClauseNode:
        return ClauseNode(
            heading=heading, text="", level=1, number=None, children=children or [],
        )

    # 1. 单个编号根（无撞号）
    bank.append(("单编号根", [numbered("总则", "1")]))
    # 2. 单个未编号根（无撞号）
    bank.append(("单未编号根", [unnumbered("引言")]))
    # 3. 多个唯一编号根（无撞号）
    bank.append(("多唯一编号根", [numbered("甲", "1"), numbered("乙", "2"), numbered("丙", "3.1")]))
    # 4. 两个撞号的顶层编号根（无未编号祖先）
    bank.append(("顶层重复编号", [numbered("甲章", "1"), numbered("乙章", "1")]))
    # 5. 正文 1 + 未编号附录（重启编号）下 1
    bank.append((
        "正文与附录重启",
        [numbered("总则", "1"), unnumbered("附录 A", [numbered("附录项", "1")])],
    ))
    # 6. 两个同名未编号附录各含 1（触发 order_index 兜底）
    bank.append((
        "同名附录各含1",
        [
            unnumbered("附录", [numbered("第一项", "1")]),
            unnumbered("附录", [numbered("第一项", "1")]),
        ],
    ))
    # 7. 多个重启编号的附录（不同标题）各含 1、2
    bank.append((
        "多附录重启编号",
        [
            numbered("正文一", "1"),
            numbered("正文二", "2"),
            unnumbered("附录 A", [numbered("A一", "1"), numbered("A二", "2")]),
            unnumbered("附录 B", [numbered("B一", "1"), numbered("B二", "2")]),
        ],
    ))
    # 8. 嵌套未编号祖先 + 深层重启编号
    bank.append((
        "嵌套未编号与深层重启",
        [
            numbered("章一", "1", [numbered("节", "1")]),
            unnumbered("附录", [unnumbered("子节", [numbered("条", "1")])]),
        ],
    ))
    # 9. >2 路撞号（三个顶层 1）
    bank.append(("三路撞号", [numbered("甲", "1"), numbered("乙", "1"), numbered("丙", "1")]))
    # 10. 未编号标题也相撞（两个「引言」）
    bank.append(("重复未编号标题", [unnumbered("引言"), unnumbered("引言")]))
    # 11. 混合：唯一编号 + 撞号编号 + 唯一未编号
    bank.append((
        "混合场景",
        [
            numbered("唯一", "3.4.2"),
            numbered("撞A", "1"),
            unnumbered("附录", [numbered("撞B", "1")]),
            unnumbered("术语表"),
        ],
    ))
    # 12. 完全退化：路径与编号都相同的重复节点（强制 order_index）
    bank.append((
        "退化重复节点",
        [
            unnumbered("卷", [numbered("条", "1")]),
            unnumbered("卷", [numbered("条", "1")]),
            unnumbered("卷", [numbered("条", "1")]),
        ],
    ))

    return bank


_TREE_BANK = _build_tree_bank()


def test_唯一性不变式_覆盖全部合成树():
    """不变式 1：对树库中任意合成树，flatten 产出的 citation_label 全部互不相同。"""
    for name, nodes in _TREE_BANK:
        rows = flatten(nodes, document_id=1)
        labels = [r["citation_label"] for r in rows]
        assert len(set(labels)) == len(rows), f"树「{name}」存在撞号：{labels}"


def test_确定性不变式_两次flatten结果一致():
    """不变式 2：对任意合成树，两次 flatten 产出逐字节一致（顺序稳定、确定）。"""
    for name, nodes in _TREE_BANK:
        first = flatten(nodes, document_id=1)
        second = flatten(nodes, document_id=1)
        assert first == second, f"树「{name}」两次 flatten 结果不一致"


def test_保全性不变式_无撞号树不被修复触及():
    """不变式 3：对第一趟标签本就唯一（无撞号）的树，最终标签 == 第一趟标签。

    用 _first_pass_labels 计算"未修复 flatten"的标签序列；若其本就唯一，
    则第二趟消歧应为 no-op，最终 citation_label 必须与第一趟逐一相等。
    """
    checked = 0
    for name, nodes in _TREE_BANK:
        first_pass = _first_pass_labels(nodes)
        if len(set(first_pass)) != len(first_pass):
            continue  # 有撞号的树交给唯一性/兜底不变式，本断言只管无撞号树
        checked += 1
        rows = flatten(nodes, document_id=1)
        actual = [r["citation_label"] for r in rows]
        assert actual == first_pass, f"无撞号树「{name}」被意外改写：{actual} != {first_pass}"
    assert checked > 0, "树库中应至少包含一棵无撞号树以验证保全性"


def test_order_index最终兜底_退化重复节点():
    """不变式 4：路径与编号都相同的退化重复节点仍唯一，且出现 #{order_index} 标记。

    这是确定性阶梯最后一道防线的专项断言：当最近未编号祖先与完整 heading_path
    都无法区分时，追加唯一稳定的 order_index 保证全局唯一。
    """
    nodes = [
        ClauseNode(
            heading="卷", text="", level=1, number=None,
            children=[ClauseNode(heading="条", text="第一处。", level=2, number="1")],
        ),
        ClauseNode(
            heading="卷", text="", level=1, number=None,
            children=[ClauseNode(heading="条", text="第二处。", level=2, number="1")],
        ),
    ]

    rows = flatten(nodes, document_id=1)
    labels = [r["citation_label"] for r in rows]

    assert len(set(labels)) == len(rows)
    # 两个编号条款路径编号全同，必然落到 order_index 兜底。
    编号标签 = [r["citation_label"] for r in rows if r["number"] is not None]
    assert all(f"{PATH_SEPARATOR}#" in label for label in 编号标签), labels
