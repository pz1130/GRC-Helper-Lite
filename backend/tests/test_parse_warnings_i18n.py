"""解析告警也要能翻译。

`parse_warnings` 存的是写死的中文句子，`Documents.tsx` 与 `DocumentDetail.tsx`
原样显示——和业务错误同一个病（见 test_errors.py）。治法也一样：**带一个
code 和变量**，中文那句留作兜底。

与业务错误不同的一点：告警是**存进库**的。已经在库里的行没有 code，也没法
反推——所以 code 存在一个新的可空列里，老行是 NULL，前端回落到那句中文。
不做数据迁移，也不需要。

`ParseWarning` 是 `str` 的子类，为的是**不惊动任何现有代码**：解析层到处
`"\\n".join(warnings)`、`any("文本层" in w for w in warnings)`，一改类型全得跟着动。
它就是那句话，只是额外挂了 code 和 params。
"""

from app.parsing.contract import ParseWarning
from app.parsing.shape import assess_shape
from tests.factories import parsed_document_with_one_huge_clause


def test_a_parse_warning_is_still_just_a_string():
    """现有代码把它当字符串用，这一点不能变。"""
    warning = ParseWarning("最大的一条条款装下了 72% 的正文", code="shape.one_clause_dominates")
    assert warning == "最大的一条条款装下了 72% 的正文"
    assert "72%" in warning
    lines = [warning, "另一条"]
    assert "\n".join(lines).startswith("最大的一条")


def test_it_carries_a_code_and_the_values_the_sentence_interpolates():
    warning = ParseWarning(
        "最大的一条条款装下了 72% 的正文",
        code="shape.one_clause_dominates",
        params={"share": "72%"},
    )
    assert warning.code == "shape.one_clause_dominates"
    assert warning.params == {"share": "72%"}


def test_the_shape_gate_emits_codes_not_only_sentences():
    """塌树闸门是用户最常看到的那一类告警。"""
    warnings = assess_shape(parsed_document_with_one_huge_clause())
    assert warnings, "这份构造出来的文档本该判红"
    assert all(isinstance(w, ParseWarning) for w in warnings), "还有告警没带 code"
    assert "shape.one_clause_dominates" in {w.code for w in warnings}


# ── 存库 ─────────────────────────────────────────────────────
# 告警和业务错误的差别就在这儿：它是**存进库**的。已经在库里的行没有 code、
# 也没法反推，所以 code 存在一个新的可空列里，老行是 NULL、前端回落到中文。
# 不做数据迁移，也不需要。


def test_codes_are_persisted_alongside_the_sentences():
    from app.ingest.warnings import to_codes

    warnings = [
        ParseWarning("已跳过 19 条目录行", code="parse.skipped_toc", params={"count": 19}),
        "一条还没带 code 的老告警",
    ]
    assert to_codes(warnings) == [{"code": "parse.skipped_toc", "params": {"count": 19}}]


def test_a_document_with_no_coded_warnings_stores_null_not_an_empty_list():
    """空列表和 NULL 在前端是两回事：NULL 才该回落到那句中文。"""
    from app.ingest.warnings import to_codes

    assert to_codes(["全是老告警"]) is None
    assert to_codes([]) is None


def test_the_document_model_has_somewhere_to_put_them():
    from app.ingest.models import Document

    assert "parse_warning_codes" in Document.__table__.columns
