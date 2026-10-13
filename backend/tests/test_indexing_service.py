import pytest
from sqlalchemy import select

from app.clauses.models import Clause, ClauseChunk
from app.indexing.service import pending_chunk_ids, rebuild_chunks
from app.ingest.models import DocType, Document


async def _doc_with_clauses(db_session, bodies: list[str]) -> Document:
    doc = Document(
        title="P", doc_type=DocType.PROCEDURE, file_hash="a" * 64,
        file_path="/x.pdf", original_filename="x.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    for index, body in enumerate(bodies):
        db_session.add(
            Clause(
                document_id=doc.id,
                number=f"{index + 1}",
                heading=f"Section {index + 1}",
                heading_path=f"Doc › Section {index + 1}",
                citation_label=f"{index + 1}",
                text=body,
                order_index=index,
                level=1,
            )
        )
    await db_session.flush()
    return doc


@pytest.mark.asyncio
async def test_each_short_clause_becomes_one_chunk(db_session):
    doc = await _doc_with_clauses(db_session, ["Short body one.", "Short body two."])
    assert await rebuild_chunks(db_session, document_id=doc.id) == 2


@pytest.mark.asyncio
async def test_chunk_text_carries_the_heading_path(db_session):
    doc = await _doc_with_clauses(db_session, ["Reviewed annually."])
    await rebuild_chunks(db_session, document_id=doc.id)
    chunk = await db_session.scalar(select(ClauseChunk))
    assert chunk.text.startswith("Doc › Section 1")
    assert "Reviewed annually." in chunk.text


@pytest.mark.asyncio
async def test_long_clause_is_split_into_several_chunks(db_session):
    doc = await _doc_with_clauses(db_session, ["Privileged access control. " * 200])
    count = await rebuild_chunks(db_session, document_id=doc.id)
    assert count > 1
    indexes = [c.chunk_index for c in await db_session.scalars(select(ClauseChunk))]
    assert sorted(indexes) == list(range(count))


@pytest.mark.asyncio
async def test_clause_without_body_still_gets_a_chunk(db_session):
    doc = await _doc_with_clauses(db_session, [""])
    assert await rebuild_chunks(db_session, document_id=doc.id) == 1


@pytest.mark.asyncio
async def test_rebuild_is_idempotent(db_session):
    doc = await _doc_with_clauses(db_session, ["Body."])
    first = await rebuild_chunks(db_session, document_id=doc.id)
    second = await rebuild_chunks(db_session, document_id=doc.id)
    assert first == second
    rows = list(await db_session.scalars(select(ClauseChunk)))
    assert len(rows) == first


@pytest.mark.asyncio
async def test_rebuild_clears_stale_embeddings(db_session):
    doc = await _doc_with_clauses(db_session, ["Body."])
    await rebuild_chunks(db_session, document_id=doc.id)
    chunk = await db_session.scalar(select(ClauseChunk))
    chunk.embedding = [0.5] * 1536
    chunk.embedding_model = "old-model"
    await db_session.flush()

    await rebuild_chunks(db_session, document_id=doc.id)
    fresh = await db_session.scalar(select(ClauseChunk))
    assert fresh.embedding is None
    assert fresh.embedding_model is None


@pytest.mark.asyncio
async def test_pending_returns_chunks_not_yet_embedded_with_this_model(db_session):
    doc = await _doc_with_clauses(db_session, ["A.", "B."])
    await rebuild_chunks(db_session, document_id=doc.id)
    rows = list(await db_session.scalars(select(ClauseChunk).order_by(ClauseChunk.id)))
    rows[0].embedding = [0.1] * 1536
    rows[0].embedding_model = "current-model"
    await db_session.flush()

    pending = await pending_chunk_ids(db_session, model="current-model")
    assert pending == [rows[1].id]


@pytest.mark.asyncio
async def test_changing_model_makes_everything_pending_again(db_session):
    doc = await _doc_with_clauses(db_session, ["A.", "B."])
    await rebuild_chunks(db_session, document_id=doc.id)
    for row in await db_session.scalars(select(ClauseChunk)):
        row.embedding = [0.1] * 1536
        row.embedding_model = "old-model"
    await db_session.flush()
    assert len(await pending_chunk_ids(db_session, model="new-model")) == 2
