"""测试用的构造件。"""

from app.parsing.contract import ClauseNode, DocumentMeta, ParsedDocument


def _meta() -> DocumentMeta:
    return DocumentMeta(
        title="T", version=None, owner=None, approver=None,
        approved_date=None, effective_date=None, doc_type=None,
    )


def parsed_document_with_one_huge_clause() -> ParsedDocument:
    """一条条款吞掉九成正文——`MAX_SINGLE_CLAUSE_SHARE` 判红的典型形态。"""
    huge = ClauseNode(heading="Scope", text="x" * 9000, level=1, number="1")
    small = ClauseNode(heading="Review", text="y" * 400, level=1, number="2")
    return ParsedDocument(meta=_meta(), clauses=[huge, small])
