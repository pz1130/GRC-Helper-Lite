"""解析形状检查：导入时就告诉用户"这份文档没切出可用的条款树"。

这套判据原先只活在 `tests/test_corpus_report.py` 里——开发者跑 `make corpus` 才看得到。
产品自己不做这个检查：用户上传一份切成一坨的文档，`status` 照样是 active，
界面上什么都不说，直到他点开某个控制点、发现出处指向"条款 1"才知道不对。

阈值取自 36 份实测，与语料报告**共用同一份定义**（那边现在 import 这里，
不再各写一套数字）：

    最大块占比   正常 7–33%，短文件最高 56%；塌掉的 70% / 84% / 100%
    条/千字      PDF 0.29–0.43，docx 1.31–3.73；塌的 0.03–0.10，过切的 13.34
    标题最长     正常 28–144 字；把正文当标题的 288–1051
    编号唯一率   正常 100%；段落编号被当成章节编号的 34%–92%

**只警告，不拦截。** 用户可能明知文档难啃也要先导进来看看——那是他的判断。
"""

from app.parsing.contract import ClauseNode, DocumentMeta, ParsedDocument
from app.parsing.shape import assess_shape

META = DocumentMeta(
    title=None, version=None, owner=None, approver=None,
    approved_date=None, effective_date=None, doc_type=None,
)


def _parsed(*clauses: ClauseNode) -> ParsedDocument:
    return ParsedDocument(meta=META, clauses=list(clauses), warnings=[])


def _section(heading: str, text: str, number: str | None = None) -> ClauseNode:
    return ClauseNode(heading=heading, text=text, level=1, number=number)


def test_a_healthy_document_says_nothing():
    body = "This clause states a requirement. " * 12
    parsed = _parsed(*[_section(f"Section {n}", body, str(n)) for n in range(1, 9)])

    assert assess_shape(parsed) == []


def test_one_clause_holding_the_whole_document_is_reported():
    parsed = _parsed(_section("Everything", "word " * 2000, "1"))

    assert any("条款树没切开" in warning for warning in assess_shape(parsed))


def test_too_few_clauses_for_the_text_is_reported():
    parsed = _parsed(
        _section("A", "word " * 3000, "1"),
        _section("B", "word " * 3000, "2"),
    )

    assert any("疑似塌树" in warning for warning in assess_shape(parsed))


def test_too_many_clauses_for_the_text_is_reported():
    # 每条 60 字左右、共 60 条 → 3.6k 字、每千字 16.7 条
    parsed = _parsed(*[_section(f"H{n}", "a short body line here. " * 3, str(n))
                       for n in range(1, 61)])

    assert any("正文行当成了条款" in warning for warning in assess_shape(parsed))


def test_a_heading_that_is_really_a_paragraph_is_reported():
    parsed = _parsed(
        _section("A " * 200, "word " * 400, "1"),
        *[_section(f"S{n}", "word " * 400, str(n)) for n in range(2, 8)],
    )

    assert any("那是一段正文" in warning for warning in assess_shape(parsed))


def test_repeated_clause_numbers_are_reported():
    parsed = _parsed(*[_section(f"S{n}", "word " * 300, "2") for n in range(1, 9)])

    assert any("编号不唯一" in warning for warning in assess_shape(parsed))


def test_a_document_with_no_sections_at_all_is_reported():
    tables = [ClauseNode(heading=f"Table {n}", text="word " * 300, level=1, kind="table")
              for n in range(1, 6)]
    assert any("没有任何章节条款" in warning for warning in assess_shape(_parsed(*tables)))


def test_a_short_document_is_left_alone():
    """两页的通函条款本来就少，不该因此报警。"""
    assert assess_shape(_parsed(_section("Purpose", "Short circular body.", "1"))) == []
