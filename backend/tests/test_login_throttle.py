"""登录失败限流。

全仓原先搜不到任何 rate limit / lockout——密码可以无限次猜。内网自用还能忍，
一旦部署到公网就不行。

**计数放 Redis**：天然带过期、不需要迁移，重启丢了也只是把冷却提前结束。
**Redis 不可用时放行**：GRC 系统被自己的缓存故障锁死，比"限流暂时失效"糟得多——
这是个明确的取舍，不是疏忽。
"""

from unittest.mock import AsyncMock

import pytest

from app.iam import throttle
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password


class _FakeRedis:
    """够用的假 Redis：只实现 incr / expire / delete。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.values.pop(key, None)

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake_redis(monkeypatch) -> _FakeRedis:
    redis = _FakeRedis()
    monkeypatch.setattr(throttle, "_client", AsyncMock(return_value=redis))
    return redis


async def _user(db_session, email: str = "lead@example.com") -> User:
    user = User(email=email, name=email, role=Role.GRC_LEAD,
                password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def test_a_password_can_no_longer_be_guessed_forever(client, db_session, fake_redis):
    await _user(db_session)
    body = {"email": "lead@example.com", "password": "wrong"}

    for _ in range(throttle.MAX_ACCOUNT_FAILURES):
        assert (await client.post("/api/auth/login", json=body)).status_code == 401

    blocked = await client.post("/api/auth/login", json=body)
    assert blocked.status_code == 429


async def test_the_right_password_is_refused_too_once_locked(client, db_session, fake_redis):
    """锁住的是这个账号，不是"这次请求猜得对不对"。"""
    await _user(db_session)
    for _ in range(throttle.MAX_ACCOUNT_FAILURES):
        await client.post("/api/auth/login",
                          json={"email": "lead@example.com", "password": "wrong"})

    resp = await client.post("/api/auth/login",
                             json={"email": "lead@example.com", "password": "pw123456"})
    assert resp.status_code == 429


async def test_a_successful_login_clears_the_count(client, db_session, fake_redis):
    await _user(db_session)
    for _ in range(throttle.MAX_ACCOUNT_FAILURES - 1):
        await client.post("/api/auth/login",
                          json={"email": "lead@example.com", "password": "wrong"})

    assert (await client.post(
        "/api/auth/login", json={"email": "lead@example.com", "password": "pw123456"}
    )).status_code == 200
    # 计数清零，又能再错一轮
    assert (await client.post(
        "/api/auth/login", json={"email": "lead@example.com", "password": "wrong"}
    )).status_code == 401


async def test_an_unknown_account_is_throttled_the_same_way(client, fake_redis):
    """不能因为"这个邮箱没被限流"而泄露它不存在——枚举防护不能被限流破掉。"""
    body = {"email": "nobody@example.com", "password": "wrong"}
    for _ in range(throttle.MAX_ACCOUNT_FAILURES):
        assert (await client.post("/api/auth/login", json=body)).status_code == 401

    assert (await client.post("/api/auth/login", json=body)).status_code == 429


async def test_login_still_works_when_redis_is_down(client, db_session, monkeypatch):
    """限流暂时失效，好过整个系统被自己的缓存故障锁死。"""
    await _user(db_session)
    monkeypatch.setattr(throttle, "_client", AsyncMock(side_effect=OSError("redis down")))

    resp = await client.post("/api/auth/login",
                             json={"email": "lead@example.com", "password": "pw123456"})
    assert resp.status_code == 200


async def test_a_successful_login_does_not_reset_the_ip_counter(client, db_session, fake_redis):
    """登录成功只清自己的账号计数，不能把 IP 计数一起清掉。

    按 IP 限流挡的是「拿一份邮箱列表扫」。原先成功登录会把账号和 IP 两个计数
    一起删掉，于是持有**任意一个**有效账号的人可以：错 49 次 → 用自己的账号
    登录一次 → 计数归零 → 再来，无限循环。那道闸门对唯一想防的对手失效。

    办公网共用出口 IP 的顾虑由 15 分钟的窗口兜着：计数从第一次失败起算 TTL，
    到点自然过期，不需要靠成功登录来解锁。
    """
    await _user(db_session)

    for _ in range(3):
        await client.post("/api/auth/login",
                          json={"email": "nobody@example.com", "password": "wrong"})
    ip_keys = [k for k in fake_redis.values if k.startswith("login:fail:ip:")]
    assert ip_keys, f"没有按 IP 计数，实际的键：{list(fake_redis.values)}"
    ip_key = ip_keys[0]
    assert int(await fake_redis.get(ip_key)) == 3

    assert (await client.post(
        "/api/auth/login", json={"email": "lead@example.com", "password": "pw123456"}
    )).status_code == 200

    assert await fake_redis.get("login:fail:account:lead@example.com") is None, "自己的账号计数该清"
    assert int(await fake_redis.get(ip_key)) == 3, "IP 计数不该被一次成功登录抹掉"


async def test_an_admin_password_reset_unlocks_the_account(client, db_session, fake_redis):
    """忘了密码的人往往先把自己试到冷却；管理员重设之后还要他再等 15 分钟，重设就白做了。"""
    target = await _user(db_session)
    admin = User(email="admin@example.com", name="admin", role=Role.ADMIN,
                 password_hash=hash_password("pw123456"))
    db_session.add(admin)
    await db_session.flush()
    for _ in range(throttle.MAX_ACCOUNT_FAILURES):
        await client.post("/api/auth/login",
                          json={"email": "lead@example.com", "password": "wrong"})
    token = (await client.post("/api/auth/login", json={
        "email": "admin@example.com", "password": "pw123456",
    })).json()["access_token"]

    reset = await client.patch(f"/api/users/{target.id}", json={"password": "new-pass-1"},
                               headers={"Authorization": f"Bearer {token}"})
    assert reset.status_code == 200

    resp = await client.post("/api/auth/login",
                             json={"email": "lead@example.com", "password": "new-pass-1"})
    assert resp.status_code == 200
