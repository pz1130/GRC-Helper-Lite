import pytest
from sqlalchemy import select, text

from app.clauses.models import Clause
from app.ingest.models import DocStatus, DocType, Document


async def _doc(db_session, **kwargs) -> Document:
    defaults = dict(
        title="IT Change Management Procedure",
        doc_type=DocType.PROCEDURE,
        file_hash="a" * 64,
        file_path="/data/documents/aa/aaaa.pdf",
        original_filename="Acme-change-management.pdf",
    )
    defaults.update(kwargs)
    doc = Document(**defaults)
    db_session.add(doc)
    await db_session.flush()
    return doc


@pytest.mark.asyncio
async def test_document_defaults_to_uploaded(db_session):
    doc = await _doc(db_session)
    assert doc.status is DocStatus.UPLOADED
    assert doc.ocr_quality_flag is False
    assert doc.supersedes_id is None


@pytest.mark.asyncio
async def test_status_persists_spec_value_not_enum_name(db_session):
    await _doc(db_session, status=DocStatus.PARSE_FAILED, parse_error="加密的 PDF")
    raw = await db_session.scalar(text("select status from documents limit 1"))
    assert raw == "parse_failed"


@pytest.mark.asyncio
async def test_file_hash_is_unique(db_session):
    from sqlalchemy.exc import IntegrityError

    await _doc(db_session)
    with pytest.raises(IntegrityError):
        await _doc(db_session, title="另一份", original_filename="other.pdf")


@pytest.mark.asyncio
async def test_version_chain(db_session):
    old = await _doc(db_session, file_hash="b" * 64, version="1.0")
    new = await _doc(
        db_session, file_hash="c" * 64, version="2.0", supersedes_id=old.id
    )
    assert new.supersedes_id == old.id


@pytest.mark.asyncio
async def test_clause_number_may_be_null(db_session):
    doc = await _doc(db_session)
    clause = Clause(
        document_id=doc.id,
        number=None,
        heading="Periodic Review",
        heading_path="Introduction › Periodic Review",
        citation_label="Introduction › Periodic Review",
        text="To ensure the continued validity...",
        order_index=5,
        level=2,
    )
    db_session.add(clause)
    await db_session.flush()

    found = await db_session.scalar(select(Clause))
    assert found.number is None
    assert found.citation_label == "Introduction › Periodic Review"


@pytest.mark.asyncio
async def test_clause_tree_is_self_referencing(db_session):
    doc = await _doc(db_session)
    parent = Clause(
        document_id=doc.id,
        number="4",
        heading="Change Management Process",
        heading_path="Change Management Process",
        citation_label="4",
        text="",
        order_index=1,
        level=1,
    )
    db_session.add(parent)
    await db_session.flush()

    child = Clause(
        document_id=doc.id,
        parent_id=parent.id,
        number="4.1",
        heading="Normal Change",
        heading_path="Change Management Process › Normal Change",
        citation_label="4.1",
        text="",
        order_index=2,
        level=2,
    )
    db_session.add(child)
    await db_session.flush()
    assert child.parent_id == parent.id


@pytest.mark.asyncio
async def test_deleting_document_removes_its_clauses(db_session):
    doc = await _doc(db_session)
    db_session.add(
        Clause(
            document_id=doc.id,
            number="1",
            heading="Scope",
            heading_path="Scope",
            citation_label="1",
            text="x",
            order_index=1,
            level=1,
        )
    )
    await db_session.flush()

    await db_session.delete(doc)
    await db_session.flush()
    assert await db_session.scalar(select(Clause)) is None
