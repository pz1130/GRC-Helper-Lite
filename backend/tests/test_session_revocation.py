"""改密码要让已经发出去的会话失效（KNOWN_ISSUES K14）。

原来令牌是 8 小时有效的 JWT、只带 sub/role/exp：管理员重设密码后，拿着旧令牌的人
（比如偷到令牌的人）照样能用到过期。现在用户带一个 token_version，令牌里记着签发时
的版本；改密码时版本加一，旧令牌立即失效。
"""

import jwt
import pytest

from app.config import get_settings
from app.iam import throttle
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password


async def _seed(db_session, role: Role, email: str) -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _login(client, email: str, password: str = "pw123456") -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_an_admin_password_reset_ends_the_users_existing_sessions(client, db_session):
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    target = await _seed(db_session, Role.VIEWER, "target@example.com")
    admin = await _login(client, "admin@example.com")
    stolen = await _login(client, "target@example.com")
    assert (await client.get("/api/auth/me", headers=stolen)).status_code == 200

    reset = await client.patch(f"/api/users/{target.id}", json={"password": "new-pass-1"}, headers=admin)
    assert reset.status_code == 200

    revoked = await client.get("/api/auth/me", headers=stolen)
    assert revoked.status_code == 401
    assert revoked.json()["code"] == "auth.session_revoked"
    fresh = await _login(client, "target@example.com", "new-pass-1")
    assert (await client.get("/api/auth/me", headers=fresh)).status_code == 200


@pytest.mark.asyncio
async def test_other_edits_do_not_end_sessions(client, db_session):
    """改名、改角色不动密码，不该把人踢下线；权限本来就按库里的角色实时判断。"""
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    target = await _seed(db_session, Role.VIEWER, "target@example.com")
    admin = await _login(client, "admin@example.com")
    session = await _login(client, "target@example.com")

    await client.patch(f"/api/users/{target.id}", json={"name": "Renamed", "role": "contributor"}, headers=admin)
    assert (await client.get("/api/auth/me", headers=session)).status_code == 200


@pytest.mark.asyncio
async def test_a_token_issued_before_versioning_still_works_until_the_password_changes(client, db_session):
    """升级不能把所有人踢下线：旧令牌没有 ver，按 0 算，与未改过密码的账号一致。"""
    user = await _seed(db_session, Role.VIEWER, "old@example.com")
    legacy = jwt.encode({"sub": str(user.id), "role": "viewer", "exp": 4102444800},
                        get_settings().jwt_secret, algorithm="HS256")
    headers = {"Authorization": f"Bearer {legacy}"}
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_a_user_can_change_their_own_password(client, db_session):
    from sqlalchemy import select

    from app.iam.models import AuditLog

    await _seed(db_session, Role.VIEWER, "me@example.com")
    laptop = await _login(client, "me@example.com")
    phone = await _login(client, "me@example.com")

    resp = await client.post("/api/auth/password", headers=laptop,
                             json={"current_password": "pw123456", "new_password": "new-pass-1"})
    assert resp.status_code == 200
    renewed = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    # 改密码的这个会话拿到新令牌继续用；另一台设备上的旧会话失效
    assert (await client.get("/api/auth/me", headers=renewed)).status_code == 200
    assert (await client.get("/api/auth/me", headers=phone)).status_code == 401
    assert (await client.get("/api/auth/me", headers=laptop)).status_code == 401
    await _login(client, "me@example.com", "new-pass-1")

    entry = await db_session.scalar(select(AuditLog).where(AuditLog.action == "user.password_change"))
    assert entry is not None
    assert "new-pass-1" not in str(entry.after) and "$" not in str(entry.after)


@pytest.mark.asyncio
async def test_a_wrong_current_password_is_refused_without_signing_the_user_out(client, db_session):
    """401 会让前端直接登出——输错当前密码不该有这个后果。"""
    await _seed(db_session, Role.VIEWER, "me@example.com")
    session = await _login(client, "me@example.com")

    resp = await client.post("/api/auth/password", headers=session,
                             json={"current_password": "wrong", "new_password": "new-pass-1"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "auth.current_password_incorrect"
    assert (await client.get("/api/auth/me", headers=session)).status_code == 200
    await _login(client, "me@example.com")  # 旧密码仍然有效


@pytest.mark.asyncio
async def test_a_new_password_must_meet_the_length_rule(client, db_session):
    await _seed(db_session, Role.VIEWER, "me@example.com")
    session = await _login(client, "me@example.com")
    resp = await client.post("/api/auth/password", headers=session,
                             json={"current_password": "pw123456", "new_password": "short"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_guessing_the_current_password_is_throttled(client, db_session, monkeypatch):
    """拿着偷来的会话，不能靠这个接口无限次猜当前密码。"""
    from unittest.mock import AsyncMock

    from tests.test_login_throttle import _FakeRedis

    monkeypatch.setattr(throttle, "_client", AsyncMock(return_value=_FakeRedis()))
    await _seed(db_session, Role.VIEWER, "me@example.com")
    session = await _login(client, "me@example.com")
    body = {"current_password": "wrong", "new_password": "new-pass-1"}
    for _ in range(throttle.MAX_ACCOUNT_FAILURES):
        assert (await client.post("/api/auth/password", headers=session, json=body)).status_code == 400
    blocked = await client.post("/api/auth/password", headers=session, json=body)
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_the_cli_password_reset_ends_sessions_too(client, db_session):
    """管理员把自己锁死之后的退路是 CLI 的 create-admin——它重设密码也要踢掉旧会话。"""
    from app.cli import create_admin

    await _seed(db_session, Role.ADMIN, "admin@example.com")
    session = await _login(client, "admin@example.com")
    await create_admin("admin@example.com", "Admin", "new-pass-1", session=db_session)
    await db_session.flush()
    assert (await client.get("/api/auth/me", headers=session)).status_code == 401
