from app.parsing.numbering import (
    NumberedHeading,
    assemble_tree,
    extract_headings,
    is_list_item,
    is_page_footer,
    is_toc_line,
    is_version_history_line,
)

REAL_SHAPED_LINES = [
    "Change Log",
    "1.0        01/06/2021 Start version of the Procedure",
    "2.0        20/09/2022 Re-written according to Enterprise approach",
    "2.01       01/12/2022 Spelling errors fixed",
    "Table of Contents",
    "1 Role and Responsibility ..................................... 3",
    "4 Change Management Process .................................. 5",
    "4.1 Normal Change ............................................ 5",
    "1 Role and Responsibility",
    "The IT Division owns this procedure.",
    "4 Change Management Process",
    "4.1 Normal Change",
    "Normal changes follow the CAB cycle.",
    "4.1.1 Change Request",
    "A change request must be raised in the tool.",
]


def test_detects_toc_lines_by_dot_leaders():
    assert is_toc_line("4.1 Normal Change ............................ 5")
    assert not is_toc_line("4.1 Normal Change")
    assert not is_toc_line("The version is 1.0. See section 4.")


def test_detects_version_history_rows():
    assert is_version_history_line("2.01       01/12/2022 Spelling errors fixed")
    assert not is_version_history_line("4.1 Normal Change")


def test_extract_drops_toc_and_version_rows():
    headings, warnings = extract_headings(REAL_SHAPED_LINES)
    numbers = [heading.number for heading in headings]

    assert numbers == ["1", "4", "4.1", "4.1.1"]
    assert "1.0" not in numbers
    assert "2.01" not in numbers
    assert len(warnings) >= 1


def test_a_lone_child_gets_its_missing_parent_synthesised():
    """原本是直接丢弃。实测 文档O 一份文档丢掉 **165 条孤儿编号**、条款数归零，
    文档B 丢掉 244 条只剩 4 条——丢的不是噪声，是整棵树。

    补出来的父节点用编号本身当标题：没有正文可依据，但空标题下游更难处理。
    """
    headings, _ = extract_headings(["4.1.1 Change Request"])

    assert [heading.number for heading in headings] == ["4", "4.1", "4.1.1"]
    assert headings[0].title == "4"
    assert headings[-1].title == "Change Request"


def test_a_synthesised_parent_does_not_displace_a_real_one_later():
    """真标题随后出现时，不能被先补出来的占位顶掉。"""
    headings, _ = extract_headings(["4.1 Detail", "4 Scope"])

    by_number = {heading.number: heading.title for heading in headings}
    assert by_number["4"] == "Scope"


def test_duplicate_number_keeps_the_first_occurrence():
    """原本写的是"保留后一次"，理由是"目录总在正文前"。真实语料证明这是错的：
    目录另有专门规则挡掉，而正文噪声（页脚、操作步骤）总在真条款**之后**——
    保留后一次会让文末噪声顶掉开头的真条款，并把它的子条款打成孤儿，整棵树就塌了。
    """
    headings, _ = extract_headings(["1 Scope", "some body text", "1 Scope"])
    assert len(headings) == 1
    assert headings[0].line_index == 0


def test_level_comes_from_dot_count():
    assert NumberedHeading(number="4", title="x", line_index=0).level == 1
    assert NumberedHeading(number="4.1", title="x", line_index=0).level == 2
    assert NumberedHeading(number="4.1.1", title="x", line_index=0).level == 3


def test_assemble_tree_nests_by_number():
    headings, _ = extract_headings(REAL_SHAPED_LINES)
    tree = assemble_tree(headings, REAL_SHAPED_LINES)
    assert tree[0].kind == "unstructured"
    tree = [node for node in tree if node.kind == "section"]

    assert [node.number for node in tree] == ["1", "4"]
    assert [node.number for node in tree[1].children] == ["4.1"]
    assert [node.number for node in tree[1].children[0].children] == ["4.1.1"]


def test_assemble_tree_captures_body_text():
    headings, _ = extract_headings(REAL_SHAPED_LINES)
    tree = assemble_tree(headings, REAL_SHAPED_LINES)
    tree = [node for node in tree if node.kind == "section"]

    assert tree[0].text == "The IT Division owns this procedure."
    assert tree[1].children[0].children[0].text == "A change request must be raised in the tool."


def test_assemble_tree_records_page_numbers():
    lines = ["1 Scope", "body", "2 Purpose"]
    tree = assemble_tree(headings=extract_headings(lines)[0], lines=lines, page_of={0: 3, 2: 4})
    assert tree[0].page_ref == 3
    assert tree[1].page_ref == 4


def test_empty_input_yields_nothing():
    assert extract_headings([]) == ([], [])
    assert assemble_tree([], []) == []


def test_the_real_trap_ratio_is_reproduced():
    headings, _ = extract_headings(REAL_SHAPED_LINES)
    numbered_lines = [
        line for line in REAL_SHAPED_LINES if line[:1].isdigit() and " " in line
    ]
    # 3 个版本历史行 + 3 个目录行 + 4 个正文条款行。
    assert len(numbered_lines) == 10
    assert len(headings) == 4


# ── 真实语料上暴露出来的三类噪声（首版全部漏进去，把 4/6 份文件的树打塌了）──


def test_detects_toc_lines_that_end_with_a_page_number():
    """实测：这批文件的目录不带引导点，而是行尾跟页码，共 84 条。"""
    assert is_toc_line("1 Introduction 1")
    assert is_toc_line("3.5 VaultKeeper Procedure 6")
    assert not is_toc_line("3.5 VaultKeeper Procedure")
    assert not is_toc_line("2 Role and Responsibility of the owner")


def test_detects_page_footers():
    """pdfplumber 把字距拉开的页脚渲染成 '1 | P a g e'，实测 17 条。"""
    assert is_page_footer("1 | P a g e")
    assert is_page_footer("10 | P a g e")
    assert is_page_footer("Page 3 of 18")
    assert not is_page_footer("1 Introduction")


def test_numbered_list_items_are_not_clauses():
    """'1. Open the settings panel' 是操作步骤。

    这条规则**本身没变**，变的是它什么时候生效：由 headings.acts_as_headings
    按整篇文档判断，不再是全局常量。见下面两条。
    """
    assert is_list_item("1", ".")
    assert is_list_item("6", ".")
    assert not is_list_item("4", ""), "真标题不带点"
    assert not is_list_item("4.1", "."), "多段带点仍按标题处理，留余地"


def test_dotted_top_level_numbers_survive_when_they_have_children():
    """文档A / 文档B / 文档E 的写法：标题编号一律带点，而且各有下级。

    旧规则把这类顶层标题整批丢掉，子标题跟着变孤儿——文档A 10 页最后只剩 1 条。
    """
    headings, _ = extract_headings([
        "1. Introduction", "1.1. Background", "1.2. Application",
        "2. Supervisory approach", "2.1. Risk-based", "2.2. Assessment",
    ])

    assert [heading.number for heading in headings] == ["1", "1.1", "1.2", "2", "2.1", "2.2"]


def test_dotted_numbers_without_children_are_still_list_items():
    """文档C 的写法：147 个 `N.`，没有一个带子号——那是正文列表，不是章节。"""
    headings, _ = extract_headings([
        "1. Boards should approve the strategy",
        "2. Boards should oversee implementation",
        "3. Boards should review outcomes",
        "Some other paragraph opening a new section",
        "1. Senior management should be qualified",
        "2. Senior management should be accountable",
    ])

    assert headings == []


def test_list_item_that_collides_with_nothing_is_still_dropped():
    """隔离"列表项过滤"这条规则本身。

    与真条款撞号的列表项会被"保留第一次"顺带挡掉，所以必须用一个**不撞号**的
    编号来验证——否则这条规则被删掉也没有任何测试会红（变异测试实测发现）。
    """
    headings, _ = extract_headings(
        ["1 Introduction", "2 Scope", "text", "7. Open the settings panel"]
    )
    assert [h.number for h in headings] == ["1", "2"], "7. 是操作步骤，不该成为条款"


def test_footer_does_not_hijack_a_real_clause_number():
    """实测事故：'2 | P a g e' 顶掉了真正的 '2 Roles and Responsibilities'。"""
    headings, _ = extract_headings(
        ["1 Introduction", "2 Roles and Responsibilities", "body", "2 | P a g e"]
    )
    assert [(h.number, h.title) for h in headings] == [
        ("1", "Introduction"),
        ("2", "Roles and Responsibilities"),
    ]


def test_list_item_does_not_hijack_a_real_clause_number():
    """实测事故：PAM 的顶层条款变成了 VaultKeeper 的操作步骤。"""
    headings, _ = extract_headings(
        ["1 Introduction", "2 Role and Responsibility", "3 Background",
         "1. Open the account list and choose New",
         "2. Pick the account type",
         "3. Pick the target platform"]
    )
    assert [h.title for h in headings] == [
        "Introduction", "Role and Responsibility", "Background"
    ]


def test_body_noise_cannot_overwrite_an_earlier_real_clause():
    """与上面那条同一规则的另一面：文末的 "1 Add account" 不得顶掉 "1 Introduction"。"""
    headings, _ = extract_headings(["1 Introduction", "body", "1 Add account"])
    assert [h.title for h in headings] == ["Introduction"]


def test_tree_does_not_collapse_when_body_noise_repeats_a_number():
    """首版的连锁事故：父节点被文末噪声顶到末尾后，子条款全成了顶层。"""
    lines = [
        "1 Introduction",
        "1.1 Purpose and Objective",
        "1.2 Scope and Applicability",
        "text",
        "1. Open the settings panel",
    ]
    tree = assemble_tree(*extract_headings(lines)[:1], lines)
    assert [n.number for n in tree] == ["1"]
    assert [c.number for c in tree[0].children] == ["1.1", "1.2"]


def test_warnings_name_every_filter_that_fired():
    _, warnings = extract_headings(
        ["1 Introduction 1", "1 | P a g e", "1. step", "2.0 01/06/2021 v1"]
    )
    joined = " ".join(warnings)
    for label in ("目录行", "页脚行", "正文列表项"):
        assert label in joined


def test_the_orphan_warning_says_filled_not_skipped():
    """补出来的父节点不能报成「已跳过」——那是相反的意思。

    另外五档告警说的是丢掉的行（目录、页脚、列表项…），orphan 说的是**补出来的**
    父节点。原先六档共用「已跳过 N 条X」的模板，于是中文界面把「补出 165 条」
    说成「跳过 165 条」，而英文词条写的是 Filled in——两边正好相反，而且因为
    i18n-check 只比对键和变量，不比对语义，它一直没被发现。
    """
    # 4.1.1 的父级 4 与 4.1 都不在文中，解析器应当补出来
    headings, warnings = extract_headings(["4.1.1 Change Request"])

    numbers = [h.number for h in headings]
    assert numbers == ["4", "4.1", "4.1.1"], "父级要补出来，不是丢掉"

    orphan = [w for w in warnings if getattr(w, "code", None) == "parse.filled_orphan"]
    assert len(orphan) == 1, f"应当有一条补出告警，实际 {[getattr(w, 'code', w) for w in warnings]}"
    assert orphan[0].params == {"count": 2}
    assert "补出" in str(orphan[0]) and "跳过" not in str(orphan[0])
