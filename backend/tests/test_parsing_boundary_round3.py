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


from app.parsing.numbering import NumberedHeading  # noqa: E402
from app.parsing.pdf_parser import _UNNUMBERED, _ends_paragraph, _glossary_terms  # noqa: E402


def test_glossary_bold_terms_become_children_of_the_glossary():
    lines = ["Appendix C. Glossary", "CSF Category", "A group of outcomes.", "CSF Core",
             "A taxonomy.", "Plain sentence not a term"]
    bold = {0, 1, 3}
    out = _glossary_terms([NumberedHeading("Appendix C", "Glossary", 0)], lines, lambda i: i in bold)
    assert [(h.title, h.number) for h in out] == [
        ("CSF Category", f"Appendix C.{_UNNUMBERED}1"), ("CSF Core", f"Appendix C.{_UNNUMBERED}3")]


def test_bold_lines_outside_a_glossary_are_ignored():
    lines = ["2. Scope", "Bold emphasis", "body."]
    assert _glossary_terms([NumberedHeading("2", "Scope", 0)], lines, lambda i: i < 2) == []


def test_bold_subheading_needs_a_finished_previous_paragraph():
    assert _ends_paragraph(["the root cause.", "Work with Forensic Investigators"], 1)
    assert not _ends_paragraph(["the team will include members from", "Legal"], 1)
