"""英文标题的几种读法：十进制、Article/Section、(a)(b)、全大写无编号。

一份文档内部用现有的唯一率 × 父子 × 递增挑树，不按某批语料调阈值。
十进制已经能切开的文档必须仍走十进制，不能被全大写行抢走。
"""

from app.parsing.numbering import assemble_tree, extract_headings


def test_article_headings_form_a_tree_when_there_is_no_decimal_outline():
    lines = [
        "Article 1 Scope",
        "This policy applies to all staff.",
        "Article 2 Ownership",
        "IT Division owns this policy.",
        "Article 3 Review",
        "The board reviews it annually.",
    ]
    headings, _ = extract_headings(lines)
    assert [heading.number for heading in headings] == ["1", "2", "3"]
    assert headings[0].title == "Scope"
    tree = assemble_tree(headings, lines)
    assert [node.heading for node in tree] == ["Scope", "Ownership", "Review"]
    assert "all staff" in tree[0].text


def test_section_headings_are_read_the_same_way_as_articles():
    lines = [
        "Section 1 Purpose",
        "State the purpose.",
        "Section 2 Composition",
        "The committee has five members.",
    ]
    headings, _ = extract_headings(lines)
    assert [heading.number for heading in headings] == ["1", "2"]


def test_a_decimal_outline_is_kept_even_if_the_file_also_has_all_caps_lines():
    """全大写行打分会虚高（赋了 1,2,3…），不能压过已经自洽的十进制树。"""
    lines = [
        "INTRODUCTION",
        "1 Introduction",
        "The purpose of this procedure is to establish controls.",
        "1.1 Background",
        "This section provides context.",
        "2 Roles and Responsibilities",
        "IT Division owns the process.",
        "ROLES AND RESPONSIBILITIES",
    ]
    headings, _ = extract_headings(lines)
    assert [heading.number for heading in headings] == ["1", "1.1", "2"]


def test_repeating_dotted_lists_lose_to_an_article_outline():
    lines = [
        "Article 1 Governance",
        "1. Open the settings panel",
        "2. Enter the name",
        "3. Save",
        "Article 2 Logging",
        "1. Open the log",
        "2. Export",
    ]
    headings, _ = extract_headings(lines)
    assert [heading.number for heading in headings] == ["1", "2"]
    assert headings[0].title == "Governance"


def test_all_caps_lines_are_a_fallback_when_nothing_numbered_is_consistent():
    lines = [
        "INTRODUCTION",
        "The purpose of this procedure is to establish controls.",
        "ROLES AND RESPONSIBILITIES",
        "IT Division owns the process.",
        "REVIEW AND APPROVAL",
        "The board reviews it annually.",
    ]
    headings, _ = extract_headings(lines)
    assert [heading.title for heading in headings] == [
        "INTRODUCTION",
        "ROLES AND RESPONSIBILITIES",
        "REVIEW AND APPROVAL",
    ]
    tree = assemble_tree(headings, lines)
    assert "establish controls" in tree[0].text


def test_a_single_run_of_lettered_items_reads_as_headings():
    lines = [
        "(a) Purpose",
        "State the purpose.",
        "(b) Scope",
        "All staff.",
        "(c) Ownership",
        "IT Division.",
    ]
    headings, _ = extract_headings(lines)
    assert [heading.number for heading in headings] == ["1", "2", "3"]
    assert headings[1].title == "Scope"


def test_lettered_items_that_restart_are_not_a_tree():
    lines = [
        "(a) First list item in section one",
        "(b) Second list item in section one",
        "(a) First list item in section two",
        "(b) Second list item in section two",
        "(c) Third list item in section two",
    ]
    headings, _ = extract_headings(lines)
    assert headings == []
