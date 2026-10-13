from unittest.mock import AsyncMock, patch

import pytest

from app.clauses.models import EMBEDDING_DIM, Clause, ClauseChunk
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.ingest.models import DocType, Document


async def _seed_user(db_session, role: Role, email: str) -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str) -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _corpus(db_session) -> Document:
    doc = Document(
        title="PAM", doc_type=DocType.PROCEDURE, file_hash="a" * 64,
        file_path="/x.pdf", original_filename="x.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    clause = Clause(
        document_id=doc.id, number="3.5", heading="VaultKeeper", heading_path="B › VaultKeeper",
        citation_label="3.5", text="", order_index=0, level=2,
    )
    db_session.add(clause)
    await db_session.flush()
    db_session.add(
        ClauseChunk(
            clause_id=clause.id, document_id=doc.id, chunk_index=0,
            text="Privileged accounts are vaulted in VaultKeeper.",
            embedding=[0.1] * EMBEDDING_DIM, embedding_model="m1",
        )
    )
    await db_session.flush()
    return doc


@pytest.mark.asyncio
async def test_search_requires_authentication(client):
    assert (await client.get("/api/search?q=privileged")).status_code == 401


@pytest.mark.asyncio
async def test_viewer_can_search(client, db_session):
    await _seed_user(db_session, Role.VIEWER, "v@example.com")
    await _corpus(db_session)
    headers = await _auth(client, "v@example.com")
    with patch("app.search.service.current_model", new=AsyncMock(return_value="m1")):
        with patch(
            "app.search.service.embed",
            new=AsyncMock(return_value=([[0.1] * EMBEDDING_DIM], 1)),
        ):
            resp = await client.get("/api/search?q=privileged accounts", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["hits"][0]["citation_label"] == "3.5"
    assert body["vector_used"] is True


@pytest.mark.asyncio
async def test_blank_query_returns_empty_result(client, db_session):
    await _seed_user(db_session, Role.VIEWER, "v@example.com")
    headers = await _auth(client, "v@example.com")
    resp = await client.get("/api/search?q=", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["hits"] == []


@pytest.mark.asyncio
async def test_viewer_cannot_trigger_reindex(client, db_session):
    await _seed_user(db_session, Role.VIEWER, "v@example.com")
    headers = await _auth(client, "v@example.com")
    assert (await client.post("/api/index/rebuild", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_contributor_can_trigger_reindex(client, db_session):
    await _seed_user(db_session, Role.CONTRIBUTOR, "c@example.com")
    headers = await _auth(client, "c@example.com")
    with patch("app.indexing.router.enqueue", new=AsyncMock(return_value="job-9")):
        resp = await client.post("/api/index/rebuild", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["job_id"] == "job-9"


@pytest.mark.asyncio
async def test_index_status_reports_progress(client, db_session):
    await _seed_user(db_session, Role.VIEWER, "v@example.com")
    await _corpus(db_session)
    headers = await _auth(client, "v@example.com")
    with patch("app.indexing.router.current_model", new=AsyncMock(return_value="m1")):
        resp = await client.get("/api/index/status", headers=headers)
    body = resp.json()
    assert body["total"] == 1
    assert body["embedded"] == 1
    assert body["pending"] == 0


@pytest.mark.asyncio
async def test_status_counts_stale_model_as_pending(client, db_session):
    await _seed_user(db_session, Role.VIEWER, "v@example.com")
    await _corpus(db_session)
    headers = await _auth(client, "v@example.com")
    with patch("app.indexing.router.current_model", new=AsyncMock(return_value="m2")):
        body = (await client.get("/api/index/status", headers=headers)).json()
    assert body["embedded"] == 0
    assert body["pending"] == 1
