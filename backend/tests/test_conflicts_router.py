from unittest.mock import AsyncMock, patch

import pytest

from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password


async def _user(db_session, role: Role = Role.VIEWER, email: str = "v@example.com") -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str = "v@example.com") -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_list_requires_authentication(client):
    assert (await client.get("/api/conflicts")).status_code == 401


@pytest.mark.asyncio
async def test_viewer_can_list_conflicts(client, db_session):
    await _user(db_session)
    headers = await _auth(client)
    resp = await client.get("/api/conflicts", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_detect_requires_authentication(client):
    assert (await client.post("/api/conflicts/detect")).status_code == 401


@pytest.mark.asyncio
async def test_viewer_cannot_trigger_detection(client, db_session):
    await _user(db_session, Role.VIEWER, "v@example.com")
    headers = await _auth(client, "v@example.com")
    assert (await client.post("/api/conflicts/detect", headers=headers)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role,email", [
    (Role.GRC_LEAD, "lead@example.com"),
    (Role.ADMIN, "admin@example.com"),
])
async def test_lead_or_admin_enqueues_detection(client, db_session, role, email):
    await _user(db_session, role, email)
    headers = await _auth(client, email)
    with patch("app.conflicts.router.enqueue", new=AsyncMock(return_value="job-1")):
        resp = await client.post("/api/conflicts/detect", headers=headers)
    assert resp.status_code == 202
    assert resp.json() == {"job_id": "job-1"}


@pytest.mark.asyncio
async def test_detection_can_be_limited_to_an_acceptance_sample(client, db_session):
    await _user(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")
    enqueue = AsyncMock(return_value="job-1")
    with patch("app.conflicts.router.enqueue", new=enqueue):
        resp = await client.post(
            "/api/conflicts/detect?max_batches=10", headers=headers
        )

    assert resp.status_code == 202
    enqueue.assert_awaited_once_with("detect_conflicts", 10)
