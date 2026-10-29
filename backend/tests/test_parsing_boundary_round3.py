"""边界问题第三轮（真实语料 v2 弱项）的回归测试。"""
from app.parsing.contract import ClauseNode
from app.parsing.pdf_parser import _has_decimal_paragraphs


def _s(number, heading, text=""):
    return ClauseNode(heading=heading, text=text, level=len(number.split(".")), number=number)


def test_decimal_paragraph_doc_keeps_n_dot_lists_in_body():
    # SANS AUP：4.1.1/4.1.2 是没标题的段落，节内 1. 2. 3. 是列表，不再拆成段落。
    nodes = [_s("4.1", "General Use"), _s("4.1.1", "4.1.1"),
             _s("4.1.2", "Employees are", "responsible for ..."), _s("4.3.2", "Email Activities")]
    assert _has_decimal_paragraphs(nodes)


def test_titled_subsections_still_allow_paragraph_numbers():
    nodes = [_s("3.1", "Scope"), _s("3.1.1", "Applicability", "This applies."),
             _s("3.1.2", "Exclusions", "None.")]
    assert not _has_decimal_paragraphs(nodes)


def test_single_untitled_paragraph_is_not_enough():
    assert not _has_decimal_paragraphs([_s("1.1.1", "1.1.1"), _s("1.1.2", "Roles", "Text.")])
