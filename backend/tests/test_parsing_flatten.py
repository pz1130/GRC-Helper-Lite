import pytest
from sqlalchemy import select

from app.clauses.models import Clause
from app.ingest.models import DocType, Document
from app.parsing.contract import ClauseNode
from app.parsing.flatten import build_citation_label, flatten, persist

TREE = [
    ClauseNode(
        heading="Change Management Process",
        text="Overview.",
        level=1,
        number="4",
        children=[
            ClauseNode(
                heading="Normal Change",
                text="CAB cycle.",
                level=2,
                number="4.1",
                children=[
                    ClauseNode(
                        heading="Change Request", text="Raise it.", level=3, number="4.1.1"
                    )
                ],
            )
        ],
    ),
    ClauseNode(heading="Table 1", text="Term | Definition", level=1, kind="table"),
]

DOCX_TREE = [
    ClauseNode(
        heading="Introduction",
        text="",
        level=1,
        children=[ClauseNode(heading="Periodic Review", text="Annually.", level=2)],
    )
]


def test_citation_label_prefers_a_real_number():
    assert build_citation_label("4.1.1", "A › B › C") == "4.1.1"


def test_citation_label_falls_back_to_heading_path():
    assert build_citation_label(None, "Introduction › Periodic Review") == (
        "Introduction › Periodic Review"
    )


def test_flatten_preserves_document_order():
    rows = flatten(TREE, document_id=1)
    assert [row["heading"] for row in rows] == [
        "Change Management Process",
        "Normal Change",
        "Change Request",
        "Table 1",
    ]
    assert [row["order_index"] for row in rows] == [0, 1, 2, 3]


def test_flatten_builds_heading_path():
    rows = flatten(TREE, document_id=1)
    assert rows[2]["heading_path"] == (
        "Change Management Process › Normal Change › Change Request"
    )


def test_flatten_uses_number_as_citation_when_present():
    rows = flatten(TREE, document_id=1)
    assert rows[2]["citation_label"] == "4.1.1"


def test_flatten_uses_path_as_citation_for_docx():
    rows = flatten(DOCX_TREE, document_id=1)
    assert rows[1]["number"] is None
    assert rows[1]["citation_label"] == "Introduction › Periodic Review"


def test_flatten_keeps_table_kind():
    rows = flatten(TREE, document_id=1)
    assert rows[3]["kind"] == "table"


@pytest.mark.asyncio
async def test_persist_writes_parent_links(db_session):
    doc = Document(
        title="t",
        doc_type=DocType.PROCEDURE,
        file_hash="d" * 64,
        file_path="/x.pdf",
        original_filename="x.pdf",
    )
    db_session.add(doc)
    await db_session.flush()

    count = await persist(db_session, TREE, document_id=doc.id)
    assert count == 4

    rows = list(await db_session.scalars(select(Clause).order_by(Clause.order_index)))
    by_heading = {row.heading: row for row in rows}
    assert by_heading["Normal Change"].parent_id == by_heading[
        "Change Management Process"
    ].id
    assert by_heading["Change Request"].parent_id == by_heading["Normal Change"].id
    assert by_heading["Change Management Process"].parent_id is None


@pytest.mark.asyncio
async def test_persist_is_idempotent_for_reparse(db_session):
    doc = Document(
        title="t",
        doc_type=DocType.PROCEDURE,
        file_hash="e" * 64,
        file_path="/x.pdf",
        original_filename="x.pdf",
    )
    db_session.add(doc)
    await db_session.flush()

    await persist(db_session, TREE, document_id=doc.id)
    await persist(db_session, TREE, document_id=doc.id)

    rows = list(await db_session.scalars(select(Clause)))
    assert len(rows) == 4
