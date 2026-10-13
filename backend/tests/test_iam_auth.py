from datetime import UTC, datetime, timedelta

import pytest

from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password


async def _make_user(db_session, **kwargs) -> User:
    defaults = dict(
        email="lead@example.com",
        name="GRC Lead",
        role=Role.GRC_LEAD,
        password_hash=hash_password("pw123456"),
    )
    defaults.update(kwargs)
    user = User(**defaults)
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_login_returns_token(client, db_session):
    await _make_user(db_session)
    resp = await client.post(
        "/api/auth/login", json={"email": "lead@example.com", "password": "pw123456"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["role"] == "grc_lead"


@pytest.mark.asyncio
async def test_login_rejects_wrong_password(client, db_session):
    await _make_user(db_session)
    resp = await client.post(
        "/api/auth/login", json={"email": "lead@example.com", "password": "nope"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_inactive_user(client, db_session):
    await _make_user(db_session, is_active=False)
    resp = await client.post(
        "/api/auth/login", json={"email": "lead@example.com", "password": "pw123456"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_expired_auditor_account(client, db_session):
    """spec §8.1：外部审计员账号可设有效期，过期即不能登录。"""
    await _make_user(
        db_session,
        email="auditor@example.com",
        role=Role.VIEWER,
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    resp = await client.post(
        "/api/auth/login", json={"email": "auditor@example.com", "password": "pw123456"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unknown_email_still_runs_a_password_verify(client, monkeypatch):
    """只统一文案不统一耗时挡不住账号枚举，账号不存在也必须跑一次等价校验。"""
    calls: list[str] = []
    monkeypatch.setattr("app.iam.router.dummy_verify", lambda raw: calls.append(raw))

    resp = await client.post(
        "/api/auth/login", json={"email": "ghost@example.com", "password": "some-pw"}
    )
    assert resp.status_code == 401
    assert calls == ["some-pw"]


@pytest.mark.asyncio
async def test_me_requires_token(client):
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_me_returns_current_user(client, db_session):
    await _make_user(db_session)
    login = await client.post(
        "/api/auth/login", json={"email": "lead@example.com", "password": "pw123456"}
    )
    token = login.json()["access_token"]
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "lead@example.com"


@pytest.mark.asyncio
async def test_login_response_never_contains_password_hash(client, db_session):
    await _make_user(db_session)
    resp = await client.post(
        "/api/auth/login", json={"email": "lead@example.com", "password": "pw123456"}
    )
    assert "password_hash" not in resp.text
