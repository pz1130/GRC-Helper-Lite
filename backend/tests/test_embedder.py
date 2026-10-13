from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.clauses.models import Clause, ClauseChunk
from app.crypto import encrypt
from app.indexing.embedder import BATCH_SIZE, current_model, embed_pending
from app.indexing.service import rebuild_chunks
from app.ingest.models import DocType, Document
from app.llm.models import LLMProviderConfig, ProviderKind, TaskRouting


async def _setup(db_session, bodies: list[str]) -> Document:
    config = LLMProviderConfig(
        name="emb", kind=ProviderKind.OPENAI, model="text-embedding-3-large",
        api_key_encrypted=encrypt("k"),
    )
    db_session.add(config)
    await db_session.flush()
    db_session.add(TaskRouting(task_key="embedding", provider_config_id=config.id))

    doc = Document(
        title="P", doc_type=DocType.PROCEDURE, file_hash="a" * 64,
        file_path="/x.pdf", original_filename="x.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    for index, body in enumerate(bodies):
        db_session.add(
            Clause(
                document_id=doc.id, number=f"{index + 1}", heading=f"S{index + 1}",
                heading_path=f"Doc › S{index + 1}", citation_label=f"{index + 1}",
                text=body, order_index=index, level=1,
            )
        )
    await db_session.flush()
    await rebuild_chunks(db_session, document_id=doc.id)
    return doc


def _fake_embed(count: int):
    async def _impl(session, *, texts):
        return [[0.01 * i] * 1536 for i in range(len(texts))], 10 * len(texts)

    return AsyncMock(side_effect=_impl)


@pytest.mark.asyncio
async def test_current_model_comes_from_task_routing(db_session):
    await _setup(db_session, ["A."])
    assert await current_model(db_session) == "text-embedding-3-large"


@pytest.mark.asyncio
async def test_embeds_pending_chunks(db_session):
    await _setup(db_session, ["A.", "B."])
    with patch("app.indexing.embedder.embed", new=_fake_embed(2)):
        result = await embed_pending(db_session)

    assert result["embedded"] == 2
    assert result["remaining"] == 0
    rows = list(await db_session.scalars(select(ClauseChunk)))
    assert all(row.embedding is not None for row in rows)
    assert all(row.embedding_model == "text-embedding-3-large" for row in rows)


@pytest.mark.asyncio
async def test_already_embedded_chunks_are_skipped(db_session):
    await _setup(db_session, ["A.", "B."])
    with patch("app.indexing.embedder.embed", new=_fake_embed(2)):
        await embed_pending(db_session)
        second = _fake_embed(0)
        with patch("app.indexing.embedder.embed", new=second):
            result = await embed_pending(db_session)

    assert result["embedded"] == 0
    second.assert_not_awaited()


@pytest.mark.asyncio
async def test_limit_leaves_the_rest_pending(db_session):
    await _setup(db_session, [f"Body {i}." for i in range(5)])
    with patch("app.indexing.embedder.embed", new=_fake_embed(2)):
        result = await embed_pending(db_session, limit=2)
    assert result["embedded"] == 2
    assert result["remaining"] == 3


@pytest.mark.asyncio
async def test_vectors_land_on_the_right_chunks(db_session):
    await _setup(db_session, ["A.", "B.", "C."])

    async def _positional(session, *, texts):
        return [[float(len(text))] * 1536 for text in texts], 1

    with patch("app.indexing.embedder.embed", new=AsyncMock(side_effect=_positional)):
        await embed_pending(db_session)

    for chunk in await db_session.scalars(select(ClauseChunk)):
        assert chunk.embedding[0] == float(len(chunk.text))


@pytest.mark.asyncio
async def test_failure_leaves_earlier_batches_committed(db_session):
    from app.llm.providers.base import ProviderError

    await _setup(db_session, [f"Body {i}." for i in range(BATCH_SIZE + 3)])
    calls = {"n": 0}

    async def _flaky(session, *, texts):
        calls["n"] += 1
        if calls["n"] > 1:
            raise ProviderError("上游返回 500", retryable=True)
        return [[0.1] * 1536 for _ in texts], 1

    with patch("app.indexing.embedder.embed", new=AsyncMock(side_effect=_flaky)):
        with pytest.raises(ProviderError):
            await embed_pending(db_session)

    done = await db_session.scalar(
        select(ClauseChunk).where(ClauseChunk.embedding.is_not(None)).limit(1)
    )
    assert done is not None


def test_batch_size_is_explicit():
    assert BATCH_SIZE == 64
