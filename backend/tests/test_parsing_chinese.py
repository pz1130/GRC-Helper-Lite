from app.parsing.chinese import Item, cn_to_int, detect_style, match_label, segment
from app.parsing.contract import ClauseNode
from app.parsing.restructure import adopt_article_titles, demote_restarted_lists, nest_chapters


def _items(lines):
    return [Item(line, source_key=f"line:{i}", page_ref=1) for i, line in enumerate(lines)]


def test_cn_numerals():
    assert [cn_to_int(t) for t in ("一", "十", "十二", "二十一", "一百零三")] == [1, 10, 12, 21, 103]


def test_reference_to_article_is_not_a_heading():
    assert match_label("第三条规定的情形除外。") is None


def test_regulation_chapters_and_articles():
    lines = ["某某管理办法", "第一章 总则", "第一条 为了规范管理，制定本办法。",
             "第二条 本办法适用于全行。", "（一）总行；", "第二章 职责", "第三条 信息科技部负责实施。"]
    style = detect_style(lines)
    assert style == "article"
    roots = segment(_items(lines), style)
    chapters = [n for n in roots if n.kind == "section"]
    assert [c.number for c in chapters] == ["第一章", "第二章"]
    assert chapters[0].heading == "总则"
    assert [a.number for a in chapters[0].children] == ["第一条", "第二条"]
    assert "（一）总行；" in chapters[0].children[1].text


def test_outline_style_levels():
    lines = ["一、总体要求", "正文。", "（一）工作目标", "目标正文。", "二、保障措施", "保障正文。"]
    style = detect_style(lines)
    assert style == "outline"
    roots = [n for n in segment(_items(lines), style) if n.kind == "section"]
    assert [n.number for n in roots] == ["一、", "二、"]
    assert roots[0].children[0].heading == "工作目标"


def test_english_article_titles_and_chapters():
    nodes = [ClauseNode("Preamble", "Title\nCHAPTER I\nGeneral", 1, kind="unstructured"),
             ClauseNode("Article 1", "Scope\nBody one.\nCHAPTER II\nControls", 1, "1"),
             ClauseNode("Article 2", "Access\nBody two.", 1, "2")]
    adopt_article_titles(nodes)
    roots = nest_chapters(nodes)
    assert [n.number for n in roots[1:]] == ["Chapter I", "Chapter II"]
    assert roots[1].children[0].heading == "Scope"
    assert roots[1].children[0].text == "Body one."
    assert roots[2].children[0].level == 2


def test_restarted_list_is_demoted():
    long = "Record step one of the procedure in the register for audit."
    nodes = [ClauseNode("Risk", "Body.", 1, "2"), ClauseNode(long, "", 1, "1"),
             ClauseNode(long, "", 1, "2"), ClauseNode("2", "", 1, "2", children=[ClauseNode("x", "", 2, "2.1")])]
    roots = demote_restarted_lists(nodes)
    assert len(roots) == 1 and "1. Record step" in roots[0].text and roots[0].children[0].number == "2.1"
