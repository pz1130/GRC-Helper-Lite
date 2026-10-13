"""编号词汇表：认编号，不判断是不是标题。

同一个 `1.` 在一份文档里是章节标题、在另一份里是操作步骤——那是仲裁的事
（test_parsing_headings.py），词汇表只管"这行开头有没有编号、编号是什么"。
"""

import re

import pytest

from app.parsing.numbering import extract_headings
from app.parsing.vocab import VOCABULARIES, parse_label


def test_decimal_numbers_parse_with_or_without_a_trailing_dot():
    # 样本语料不带点，文档A / 文档K 带点。两种都要认出来，是不是标题另说。
    assert parse_label("3.4.2 Escalation").parts == (3, 4, 2)
    assert parse_label("1.1. Background").parts == (1, 1)
    assert parse_label("1.1. Background").trailing_dot is True
    assert parse_label("3.4.2 Escalation").trailing_dot is False


def test_the_title_comes_back_without_the_label():
    found = parse_label("1.1. Background")
    assert found.title == "Background"
    assert found.label == "1.1."


def test_parenthesised_labels_parse():
    """文档M 有 226 行这种形态，现有正则一行都认不出。"""
    assert parse_label("(a) The rules set forth in this part").kind == "paren"
    assert parse_label("(a) The rules set forth in this part").parts == (1,)
    assert parse_label("(2) Pursuant to section 907(b)").parts == (2,)


def test_letter_labels_parse():
    """文档P 的 A.–D.，制表符分隔。"""
    found = parse_label("C.\tReporting")
    assert found.kind == "letter"
    assert found.parts == (3,)
    assert found.title == "Reporting"


def test_a_sentence_is_not_a_label():
    assert parse_label("The review board meets every quarter") is None
    assert parse_label("") is None
    assert parse_label("   ") is None


def test_a_year_is_not_a_section_number():
    """没有哪份文档有第 2024 节。四位以上的单段编号一律不认——
    这是形状判断，不是文风判断。"""
    assert parse_label("2024 was a difficult year for the sector") is None
    assert parse_label("2024. Review of the year") is None
    # 但多段的不受影响：4.2024 不是年份，是编号
    assert parse_label("4.2024 Something").parts == (4, 2024)


def test_a_label_with_no_title_is_not_a_label():
    assert parse_label("1.") is None
    assert parse_label("(a)") is None


@pytest.mark.parametrize("line", ["1. Introduction", "(b) Procedures", "D. Composition"])
def test_every_vocabulary_reports_its_kind(line):
    assert parse_label(line).kind in {vocabulary.kind for vocabulary in VOCABULARIES}


def test_the_vocabulary_list_leaves_room_for_more():
    """中文词汇表（OQ-17）本期不做，但要能加进来而不改调用方。"""
    assert len(VOCABULARIES) >= 3
    for vocabulary in VOCABULARIES:
        assert callable(vocabulary.match)
        assert isinstance(vocabulary.kind, str)


# ── 两条路必须认同一套编号 ────────────────────────────────────
# docx 走 `vocab.parse_label`（没有 Heading 样式时从编号认标题，
# `docx_parser._numbered_heading`）；PDF 与「粘贴纯文本」兜底走
# `numbering.extract_headings`，而它有自己写死的三条正则。实测两套打架：
#
#     C.  Reporting lines     docx 认（letter）   PDF 不认
#     (3) The bank shall      docx 认（paren）    PDF 不认
#     Chapter 4 Controls      docx 不认           PDF 认（ARTICLE）
#
# 后果是**同一份制度，导出成 .docx 和导出成 .pdf，切出来的条款树不一样**。
# 这与中文无关，是既有缺陷；OQ-17 要的"加一个词汇表就支持中文"也因此只在
# docx 那条路上成立。下面三条把"两条路认同一套编号"钉住。


def _pdf_side(lines):
    headings, _ = extract_headings(lines)
    return [heading.title for heading in headings]


def test_pdf_path_recognises_letter_labels_like_docx_does():
    """文档P 的 A.–D.：docx 认，PDF 这条路一条都认不出。"""
    lines = [
        "A. Purpose",
        "The committee sets the risk appetite.",
        "B. Scope",
        "It covers every business line.",
        "C. Reporting",
        "Quarterly to the board.",
        "D. Review",
        "Annually by internal audit.",
    ]
    assert _pdf_side(lines) == ["Purpose", "Scope", "Reporting", "Review"]


def test_pdf_path_recognises_parenthesised_digits_like_docx_does():
    """`(1)`/`(2)` 与 `(a)`/`(b)` 是同一种形态，不该只认字母那一半。"""
    lines = [
        "(1) The bank shall maintain a register.",
        "(2) The register shall be reviewed annually.",
        "(3) Exceptions require board approval.",
    ]
    assert len(_pdf_side(lines)) == 3


def test_docx_path_recognises_the_chapter_keyword_like_pdf_does():
    """`Chapter 4` / `Article 7` / `Section 2`：PDF 认，docx 这条路不认。"""
    for line in ("Chapter 4 Controls", "Article 7 Enforcement", "Section 2 Scope"):
        found = parse_label(line)
        assert found is not None, f"docx 路不认 {line!r}"
        assert found.parts[0] in (4, 7, 2)


def test_a_new_vocabulary_becomes_a_reading_without_touching_the_parser(monkeypatch):
    """注册一种词汇表，PDF/粘贴那条路就该自动多一种读法。

    这是 OQ-17 的整个指望：中文（`第五条` / `一、`）落地时只加一个
    `Vocabulary` 实现、往 `VOCABULARIES` 里加一行，`numbering.py` 一个字不用改。
    用一个假词汇表验这条契约，免得把中文支持本身拖进来。
    """
    from dataclasses import dataclass

    from app.parsing import vocab

    @dataclass(frozen=True)
    class _Fake:
        kind: str = "fake"
        _re: re.Pattern = re.compile(r"^§(\d+)\s+(\S.*?)\s*$")

        def match(self, line):
            found = self._re.match(line)
            if found is None:
                return None
            return vocab.Numbering(
                label=f"§{found.group(1)}",
                parts=(int(found.group(1)),),
                kind=self.kind,
                title=found.group(2),
                trailing_dot=False,
            )

    monkeypatch.setattr(vocab, "VOCABULARIES", (*vocab.VOCABULARIES, _Fake()))

    headings, _ = extract_headings(
        [
            "§1 Purpose",
            "This standard sets the baseline.",
            "§2 Scope",
            "It applies to every system.",
            "§3 Review",
            "Annually by internal audit.",
        ]
    )
    assert [heading.title for heading in headings] == ["Purpose", "Scope", "Review"]
