from unittest.mock import AsyncMock, patch

import pytest

from app.clauses.models import EMBEDDING_DIM, Clause, ClauseChunk
from app.ingest.models import DocType, Document
from app.search.service import search


async def _corpus(db_session) -> Document:
    doc = Document(
        title="Access Control Procedure", doc_type=DocType.PROCEDURE,
        file_hash="a" * 64, file_path="/x.pdf", original_filename="x.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    clause = Clause(
        document_id=doc.id, number="3.5", heading="VaultKeeper Procedure",
        heading_path="Background › VaultKeeper Procedure", citation_label="3.5",
        text="", order_index=0, level=2,
    )
    db_session.add(clause)
    await db_session.flush()
    for index, body in enumerate(
        [
            "Privileged accounts are vaulted in VaultKeeper and reviewed quarterly.",
            "Change requests must be approved by the CAB before implementation.",
        ]
    ):
        db_session.add(
            ClauseChunk(
                clause_id=clause.id, document_id=doc.id, chunk_index=index, text=body,
                embedding=[0.1 * (index + 1)] * EMBEDDING_DIM, embedding_model="m1",
            )
        )
    await db_session.flush()
    return doc


@pytest.mark.asyncio
async def test_returns_hits_with_citation_and_document_title(db_session):
    await _corpus(db_session)
    with patch("app.search.service.current_model", new=AsyncMock(return_value="m1")):
        with patch(
            "app.search.service.embed",
            new=AsyncMock(return_value=([[0.1] * EMBEDDING_DIM], 1)),
        ):
            response = await search(db_session, "privileged accounts VaultKeeper")

    assert response.hits
    top = response.hits[0]
    assert top.citation_label == "3.5"
    assert top.heading_path == "Background › VaultKeeper Procedure"
    assert top.document_title == "Access Control Procedure"


@pytest.mark.asyncio
async def test_vector_used_is_reported_true_when_vectors_exist(db_session):
    await _corpus(db_session)
    with patch("app.search.service.current_model", new=AsyncMock(return_value="m1")):
        with patch(
            "app.search.service.embed",
            new=AsyncMock(return_value=([[0.1] * EMBEDDING_DIM], 1)),
        ):
            response = await search(db_session, "privileged")
    assert response.vector_used is True


@pytest.mark.asyncio
async def test_falls_back_to_fulltext_when_embedding_is_unavailable(db_session):
    from app.llm.routing import RoutingError

    await _corpus(db_session)
    with patch("app.search.service.current_model", new=AsyncMock(side_effect=RoutingError("未配置"))):
        response = await search(db_session, "privileged accounts")
    assert response.hits
    assert response.vector_used is False
    assert response.vector_unavailable_reason == "provider_unconfigured"


@pytest.mark.asyncio
async def test_embedding_call_failure_also_degrades(db_session):
    from app.llm.providers.base import ProviderError

    await _corpus(db_session)
    with patch("app.search.service.current_model", new=AsyncMock(return_value="m1")):
        with patch(
            "app.search.service.embed",
            new=AsyncMock(side_effect=ProviderError("上游 500", retryable=True)),
        ):
            response = await search(db_session, "privileged accounts")
    assert response.hits
    assert response.vector_used is False
    assert response.vector_unavailable_reason == "provider_error"


@pytest.mark.asyncio
async def test_expanded_terms_are_used_for_fulltext_and_reported(db_session):
    await _corpus(db_session)
    with patch(
        "app.search.service.expand",
        new=AsyncMock(return_value=["特权账号", "privileged accounts"]),
    ):
        with patch("app.search.service.current_model", new=AsyncMock(return_value="m1")):
            with patch(
                "app.search.service.embed",
                new=AsyncMock(return_value=([[0.1] * EMBEDDING_DIM], 1)),
            ):
                response = await search(db_session, "特权账号")
    assert "privileged accounts" in response.expanded_terms
    assert response.hits


@pytest.mark.asyncio
async def test_document_filter_is_passed_through(db_session):
    doc = await _corpus(db_session)
    with patch("app.search.service.current_model", new=AsyncMock(return_value="m1")):
        with patch(
            "app.search.service.embed",
            new=AsyncMock(return_value=([[0.1] * EMBEDDING_DIM], 1)),
        ):
            response = await search(db_session, "privileged", document_id=doc.id + 999)
    assert response.hits == []


@pytest.mark.asyncio
async def test_blank_query_returns_empty_without_calling_anything(db_session):
    embedder = AsyncMock()
    with patch("app.search.service.embed", new=embedder):
        response = await search(db_session, "   ")
    assert response.hits == []
    embedder.assert_not_awaited()


@pytest.mark.asyncio
async def test_hits_carry_both_ranks_for_debugging(db_session):
    await _corpus(db_session)
    with patch("app.search.service.current_model", new=AsyncMock(return_value="m1")):
        with patch(
            "app.search.service.embed",
            new=AsyncMock(return_value=([[0.1] * EMBEDDING_DIM], 1)),
        ):
            response = await search(db_session, "privileged accounts")
    top = response.hits[0]
    assert top.rank_fulltext is not None or top.rank_vector is not None


@pytest.mark.asyncio
async def test_a_superseded_document_is_left_out_unless_asked_for(db_session):
    # 审计答复拿检索结果当依据：已被新版替换的条文不能再被引用成现行制度。
    # 但明确在那份旧文档里搜，是在查历史，照常返回。
    from app.ingest.models import DocStatus

    doc = await _corpus(db_session)
    doc.status = DocStatus.SUPERSEDED
    await db_session.flush()

    with (
        patch("app.search.service.current_model", new=AsyncMock(return_value="m1")),
        patch(
            "app.search.service.embed",
            new=AsyncMock(return_value=([[0.1] * EMBEDDING_DIM], 1)),
        ),
    ):
        everywhere = await search(db_session, "privileged accounts VaultKeeper")
        inside = await search(db_session, "privileged accounts VaultKeeper", document_id=doc.id)

    assert everywhere.hits == []
    assert inside.hits
