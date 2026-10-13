import pytest

from app.clauses.models import EMBEDDING_DIM, Clause, ClauseChunk
from app.ingest.models import DocType, Document
from app.search.vector import search


def _vec(first: float) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = first
    vector[1] = 1.0
    return vector


async def _chunks(
    db_session,
    specs: list[tuple[str, list[float] | None, str | None]],
    *,
    doc_title: str = "P",
):
    doc = Document(
        title=doc_title, doc_type=DocType.PROCEDURE,
        file_hash=doc_title.ljust(64, "x"),
        file_path="/x.pdf", original_filename="x.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    clause = Clause(
        document_id=doc.id, number="1", heading="S", heading_path="Doc › S",
        citation_label="1", text="", order_index=0, level=1,
    )
    db_session.add(clause)
    await db_session.flush()
    for index, (body, vector, model) in enumerate(specs):
        db_session.add(
            ClauseChunk(
                clause_id=clause.id, document_id=doc.id, chunk_index=index,
                text=body, embedding=vector, embedding_model=model,
            )
        )
    await db_session.flush()
    return doc


@pytest.mark.asyncio
async def test_returns_nearest_first(db_session):
    await _chunks(
        db_session,
        [("far", _vec(-5.0), "m1"), ("near", _vec(5.0), "m1"), ("mid", _vec(0.5), "m1")],
    )
    hits = await search(db_session, _vec(5.0), model="m1")
    assert hits[0].rank == 1
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))


@pytest.mark.asyncio
async def test_chunks_without_embeddings_are_skipped(db_session):
    await _chunks(db_session, [("pending", None, None), ("done", _vec(1.0), "m1")])
    assert len(await search(db_session, _vec(1.0), model="m1")) == 1


@pytest.mark.asyncio
async def test_other_models_vectors_are_excluded(db_session):
    await _chunks(db_session, [("old", _vec(1.0), "old-model"), ("new", _vec(1.0), "new-model")])
    hits = await search(db_session, _vec(1.0), model="new-model")
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_without_model_filter_all_embedded_chunks_are_candidates(db_session):
    await _chunks(db_session, [("a", _vec(1.0), "m1"), ("b", _vec(1.0), "m2")])
    assert len(await search(db_session, _vec(1.0))) == 2


@pytest.mark.asyncio
async def test_limit_is_respected(db_session):
    await _chunks(db_session, [(f"c{i}", _vec(float(i)), "m1") for i in range(8)])
    assert len(await search(db_session, _vec(1.0), model="m1", limit=3)) == 3


@pytest.mark.asyncio
async def test_document_filter_narrows_results(db_session):
    doc = await _chunks(db_session, [("a", _vec(1.0), "m1")], doc_title="A")
    await _chunks(db_session, [("b", _vec(1.0), "m1")], doc_title="B")
    assert len(await search(db_session, _vec(1.0), model="m1", document_id=doc.id)) == 1


@pytest.mark.asyncio
async def test_score_is_similarity_not_distance(db_session):
    await _chunks(db_session, [("near", _vec(5.0), "m1"), ("far", _vec(-5.0), "m1")])
    hits = await search(db_session, _vec(5.0), model="m1")
    assert hits[0].score > hits[-1].score
