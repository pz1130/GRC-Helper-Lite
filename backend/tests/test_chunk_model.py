import pytest
from sqlalchemy import select, text

from app.clauses.models import EMBEDDING_DIM, Clause, ClauseChunk
from app.ingest.models import DocType, Document


async def _clause(db_session) -> Clause:
    doc = Document(
        title="P", doc_type=DocType.PROCEDURE, file_hash="a" * 64,
        file_path="/x.pdf", original_filename="x.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    clause = Clause(
        document_id=doc.id, number="4.1", heading="Normal Change",
        heading_path="Change Management Process › Normal Change",
        citation_label="4.1", text="Normal changes follow the CAB cycle.",
        order_index=1, level=2,
    )
    db_session.add(clause)
    await db_session.flush()
    return clause


@pytest.mark.asyncio
async def test_chunk_can_be_persisted_without_an_embedding(db_session):
    clause = await _clause(db_session)
    db_session.add(
        ClauseChunk(
            clause_id=clause.id, document_id=clause.document_id,
            chunk_index=0, text="…", language="en",
        )
    )
    await db_session.flush()
    chunk = await db_session.scalar(select(ClauseChunk))
    assert chunk.embedding is None
    assert chunk.embedding_model is None


@pytest.mark.asyncio
async def test_chunk_stores_a_vector(db_session):
    clause = await _clause(db_session)
    db_session.add(
        ClauseChunk(
            clause_id=clause.id, document_id=clause.document_id, chunk_index=0,
            text="…", embedding=[0.1] * EMBEDDING_DIM,
            embedding_model="text-embedding-3-large", embedding_version="v1",
        )
    )
    await db_session.flush()
    chunk = await db_session.scalar(select(ClauseChunk))
    assert len(chunk.embedding) == EMBEDDING_DIM
    assert chunk.embedding_model == "text-embedding-3-large"


@pytest.mark.asyncio
async def test_deleting_a_clause_removes_its_chunks(db_session):
    clause = await _clause(db_session)
    db_session.add(
        ClauseChunk(clause_id=clause.id, document_id=clause.document_id, chunk_index=0, text="x")
    )
    await db_session.flush()
    await db_session.delete(clause)
    await db_session.flush()
    assert await db_session.scalar(select(ClauseChunk)) is None


@pytest.mark.asyncio
async def test_fulltext_column_is_generated_from_text(db_session):
    clause = await _clause(db_session)
    db_session.add(
        ClauseChunk(
            clause_id=clause.id, document_id=clause.document_id, chunk_index=0,
            text="Privileged accounts must be reviewed quarterly.",
        )
    )
    await db_session.flush()
    hit = await db_session.scalar(
        text(
            "select count(*) from clause_chunks "
            "where tsv @@ plainto_tsquery('english', 'privileged account review')"
        )
    )
    assert hit == 1


@pytest.mark.asyncio
async def test_chunk_index_is_unique_per_clause(db_session):
    from sqlalchemy.exc import IntegrityError

    clause = await _clause(db_session)
    db_session.add(
        ClauseChunk(clause_id=clause.id, document_id=clause.document_id, chunk_index=0, text="a")
    )
    await db_session.flush()
    db_session.add(
        ClauseChunk(clause_id=clause.id, document_id=clause.document_id, chunk_index=0, text="b")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_embedding_dim_is_pinned(db_session):
    assert EMBEDDING_DIM == 1536
