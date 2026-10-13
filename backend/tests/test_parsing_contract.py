from pathlib import Path

import pytest

from app.parsing.contract import ClauseNode, DocumentMeta, ParsedDocument, ParseError
from app.parsing.registry import SUPPORTED_EXTENSIONS, get_parser


def test_clause_node_defaults_to_a_numberless_section():
    node = ClauseNode(heading="Periodic Review", text="…", level=2)
    assert node.number is None
    assert node.kind == "section"
    assert node.children == []


def test_clause_nodes_nest():
    child = ClauseNode(heading="Normal Change", text="", level=2, number="4.1")
    parent = ClauseNode(
        heading="Change Management Process", text="", level=1, number="4", children=[child]
    )
    assert parent.children[0].number == "4.1"


def test_parsed_document_carries_warnings():
    doc = ParsedDocument(
        meta=DocumentMeta(
            title=None,
            version=None,
            owner=None,
            approver=None,
            approved_date=None,
            effective_date=None,
            doc_type=None,
        ),
        clauses=[],
        warnings=["缺少 Roles and Responsibilities 章节"],
    )
    assert doc.ocr_used is False
    assert "Roles" in doc.warnings[0]


def test_registry_dispatches_by_extension():
    from app.parsing.docx_parser import DocxParser
    from app.parsing.pdf_parser import PdfParser

    assert isinstance(get_parser(Path("a.docx")), DocxParser)
    assert isinstance(get_parser(Path("a.pdf")), PdfParser)
    assert isinstance(get_parser(Path("A.PDF")), PdfParser)


def test_registry_rejects_unknown_extension():
    with pytest.raises(ParseError) as exc:
        get_parser(Path("sheet.xlsx"))
    assert ".xlsx" in exc.value.reason


def test_supported_extensions_matches_storage_allowlist():
    from app.ingest.storage import ALLOWED_EXTENSIONS

    assert SUPPORTED_EXTENSIONS == ALLOWED_EXTENSIONS
