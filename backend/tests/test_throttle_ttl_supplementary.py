"""throttle-ttl-persistence —— 补充单元 / 集成测试（任务 4）。

前面两个新文件已覆盖：
- `test_throttle_ttl_persistence.py`：bug 条件探查（注入首次 expire 丢失 -> 修复后键带 TTL）。
- `test_throttle_ttl_preservation.py`：保持性（固定窗口不被延长、fail-open、clear 只清账号键、
  阈值与两维度、allow 返回序列）。

本文件只补它们没正面覆盖的缺口，避免重复：

单元（真实 Docker Redis 做 TTL 断言；唯一键 + 结束清理）：
- 在「已有计数、无 TTL」的键上走一次正常 `record_failure` / `allow`，应把 TTL 补回
  （PTTL > 0）—— 直接走幂等「重新补设 TTL」路径（需求 2.2），不注入任何 expire 失败。
- 永不覆盖已有 TTL（固定窗口）：预先给键设一个明显短于满窗口的剩余 TTL，再命中一次，
  断言 PTTL 没有跳回满窗口（Lua 只在 PTTL < 0 时才 EXPIRE）；对自然递减留容差。
- 首次失败 happy path：全新键经 `record_failure` / `allow` 拿到满窗口 TTL 且计数正确自增。
- `_incr_with_ttl` 的返回值：返回自增后的计数（allow 的上限比较才能在边界上正确）。

集成（尽量走 `/api/auth/login` 全流程；否则退到函数级）：
- 账号失败累积后，账号键带 TTL -> 冷却能自行过期（不必等 15 分钟，断言 0 < PTTL <= 窗口）。
- per-IP 维度：IP 键带 TTL，且成功登录的 `clear` 从不触碰它（clear 账号键后 IP 键仍在且仍带 TTL）。

**测试后端**：凡做 TTL 断言都用 Docker 内真实 Redis（`throttle.redis_url` ->
`get_settings().redis_url`），唯一 email / IP / key + 结束清理。HTTP 集成用例要在 conftest
autouse 的内存假 Redis 之后再 monkeypatch 把 `_client` 指回真实 Redis。
"""

from uuid import uuid4

import pytest

from app.config import get_settings
from app.iam import throttle

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 真实 Redis 辅助（沿用前两个新文件的模式）
# ---------------------------------------------------------------------------


def _real_redis():
    """直连 Docker 内真实 Redis，用于 PTTL 断言（与前两个新文件同一后端）。"""
    return throttle.from_url(get_settings().redis_url)


@pytest.fixture
def real_throttle(monkeypatch):
    """把 `throttle._client` 指回真实 Redis，覆盖 conftest autouse 的内存假 Redis。

    yield 一个「直连真实 Redis 的观测连接」给用例做 PTTL / GET 断言；用例结束负责清键。
    """

    async def _client():
        return _real_redis()

    monkeypatch.setattr(throttle, "_client", _client)
    observer = _real_redis()
    yield observer


@pytest.fixture
def unique_ids():
    """每个用例一份唯一 email / IP，避免共享 Redis 上串扰。"""
    token = uuid4().hex
    return {
        "email": f"ttl-suppl-{token}@example.com",
        "ip": f"10.8.{int(token[:2], 16)}.{int(token[2:4], 16)}",
    }


async def _cleanup(*keys: str) -> None:
    real = _real_redis()
    try:
        await real.delete(*keys)
    finally:
        await real.aclose()


# 允许的「向上」容差（毫秒）：两次 PTTL 读取之间只应自然递减；给极小的时钟/取整
# 抖动留余量，但远小于一个完整窗口，足以把「跳回满窗口」挡在外面。
_TTL_UP_TOLERANCE_MS = 50


# ---------------------------------------------------------------------------
# 单元：幂等重新补设 TTL（需求 2.2）——不注入任何 expire 失败
# ---------------------------------------------------------------------------


async def test_record_failure_在无TTL键上补回TTL(real_throttle, unique_ids):
    """在「已有计数、无 TTL」的账号键上走一次**正常** `record_failure`，应把 TTL 补回。

    与 persistence 文件不同：这里不注入任何 expire 失败，纯粹预置一个无 TTL 键
    （等价于历史上 TTL 丢过），验证下一次正常命中就能幂等地补回 TTL（Lua 在 PTTL < 0
    时补 EXPIRE）。
    """
    email, ip = unique_ids["email"], unique_ids["ip"]
    account_key = throttle._keys(email, ip)[0]
    observer = real_throttle

    try:
        # 预置「有计数、无 TTL」：裸 incr 不带过期。
        await observer.incr(account_key)
        assert await observer.pttl(account_key) < 0, "前置条件：键此刻应无 TTL"

        await throttle.record_failure(email, ip)

        pttl = await observer.pttl(account_key)
        assert pttl > 0, f"正常 record_failure 应把缺失的 TTL 补回，实际 PTTL={pttl}"
        assert pttl <= throttle.WINDOW_SECONDS * 1000
        # 计数仍单调递增：预置 1 + 本次 1 = 2。
        assert int(await observer.get(account_key)) == 2
    finally:
        await observer.aclose()
        await _cleanup(*throttle._keys(email, ip))


async def test_allow_在无TTL键上补回TTL(real_throttle):
    """`allow` 与 `record_failure` 共用 `_incr_with_ttl`：无 TTL 键上一次正常 `allow`
    同样把 TTL 补回（需求 2.2）。
    """
    key = f"test:allow:suppl-reassert:{uuid4().hex}"
    observer = real_throttle

    try:
        await observer.incr(key)
        assert await observer.pttl(key) < 0, "前置条件：键此刻应无 TTL"

        allowed = await throttle.allow(key, limit=3, window_seconds=120)
        assert allowed is True, "计数 2 <= limit 3 仍应放行"

        pttl = await observer.pttl(key)
        assert pttl > 0, f"正常 allow 应把缺失的 TTL 补回，实际 PTTL={pttl}"
        assert pttl <= 120 * 1000
    finally:
        await observer.aclose()
        await _cleanup(key)


# ---------------------------------------------------------------------------
# 单元：永不覆盖已有 TTL（固定窗口）——预置一个明显短的剩余 TTL
# ---------------------------------------------------------------------------


async def test_record_failure_不覆盖已有较短TTL(real_throttle, unique_ids):
    """预先给账号键设一个明显短于满窗口的剩余 TTL，再命中一次，断言 PTTL 没有跳回满窗口。

    Lua 只在 PTTL < 0 时才 EXPIRE，故已有 TTL 绝不被覆盖；窗口锚定在第一次失败（固定窗口）。
    """
    email, ip = unique_ids["email"], unique_ids["ip"]
    account_key = throttle._keys(email, ip)[0]
    observer = real_throttle

    short_ttl_seconds = 30  # 远小于 WINDOW_SECONDS（15 分钟）
    try:
        # 预置「有计数、有一个短 TTL」的键。
        await observer.incr(account_key)
        await observer.expire(account_key, short_ttl_seconds)
        pttl_before = await observer.pttl(account_key)
        assert 0 < pttl_before <= short_ttl_seconds * 1000

        await throttle.record_failure(email, ip)

        pttl_after = await observer.pttl(account_key)
        # 不得跳回满窗口，也不得向上增长（允许自然递减）。
        assert pttl_after <= pttl_before + _TTL_UP_TOLERANCE_MS, (
            f"已有 TTL 被覆盖/延长：命中前 {pttl_before} -> 命中后 {pttl_after}"
        )
        assert pttl_after < throttle.WINDOW_SECONDS * 1000, (
            f"TTL 跳回了满窗口（PTTL={pttl_after}），固定窗口不该被重设"
        )
        assert int(await observer.get(account_key)) == 2, "计数应自增到 2"
    finally:
        await observer.aclose()
        await _cleanup(*throttle._keys(email, ip))


async def test_allow_不覆盖已有较短TTL(real_throttle):
    """`allow` 的键同样固定窗口：预置短 TTL，命中一次后不跳回满窗口。"""
    key = f"test:allow:suppl-fixed:{uuid4().hex}"
    observer = real_throttle

    window = 600  # 满窗口 10 分钟
    short_ttl_seconds = 20
    try:
        await observer.incr(key)
        await observer.expire(key, short_ttl_seconds)
        pttl_before = await observer.pttl(key)
        assert 0 < pttl_before <= short_ttl_seconds * 1000

        await throttle.allow(key, limit=3, window_seconds=window)

        pttl_after = await observer.pttl(key)
        assert pttl_after <= pttl_before + _TTL_UP_TOLERANCE_MS, (
            f"allow 覆盖了已有 TTL：命中前 {pttl_before} -> 命中后 {pttl_after}"
        )
        assert pttl_after < window * 1000, "allow 的固定窗口被重设回满窗口"
    finally:
        await observer.aclose()
        await _cleanup(key)


# ---------------------------------------------------------------------------
# 单元：首次失败 happy path（全新键拿到满窗口 TTL + 计数正确）
# ---------------------------------------------------------------------------


async def test_首次失败全新键拿到满窗口TTL(real_throttle, unique_ids):
    """全新账号键首次 `record_failure`：计数为 1，且带上落在满窗口内的 TTL。"""
    email, ip = unique_ids["email"], unique_ids["ip"]
    account_key = throttle._keys(email, ip)[0]
    observer = real_throttle

    try:
        assert await observer.get(account_key) is None, "前置条件：键尚不存在"

        await throttle.record_failure(email, ip)

        assert int(await observer.get(account_key)) == 1, "首次失败计数应为 1"
        pttl = await observer.pttl(account_key)
        assert 0 < pttl <= throttle.WINDOW_SECONDS * 1000, (
            f"首次失败应带满窗口内的 TTL，实际 PTTL={pttl}"
        )
    finally:
        await observer.aclose()
        await _cleanup(*throttle._keys(email, ip))


async def test_allow_首次调用全新键拿到满窗口TTL(real_throttle):
    """全新键首次 `allow`：放行，且带上落在满窗口内的 TTL。"""
    key = f"test:allow:suppl-first:{uuid4().hex}"
    observer = real_throttle
    window = 300

    try:
        assert await observer.get(key) is None

        allowed = await throttle.allow(key, limit=3, window_seconds=window)
        assert allowed is True
        assert int(await observer.get(key)) == 1
        pttl = await observer.pttl(key)
        assert 0 < pttl <= window * 1000, f"首次 allow 应带满窗口内 TTL，实际 PTTL={pttl}"
    finally:
        await observer.aclose()
        await _cleanup(key)


# ---------------------------------------------------------------------------
# 单元：_incr_with_ttl 返回自增后的计数（allow 的边界比较据此成立）
# ---------------------------------------------------------------------------


async def test_incr_with_ttl_返回自增后的计数(real_throttle):
    """`_incr_with_ttl` 必须返回自增后的计数值，连续调用得到 1,2,3...

    这是 `allow` 的 `int(count) <= limit` 判定正确的前提——若返回值不随每次调用递增，
    边界（第 limit 次 True、第 limit+1 次 False）就会错位。
    """
    key = f"test:incr:suppl-return:{uuid4().hex}"
    observer = real_throttle

    try:
        redis = _real_redis()
        try:
            counts = [
                await throttle._incr_with_ttl(redis, key, 120) for _ in range(4)
            ]
        finally:
            await redis.aclose()

        assert counts == [1, 2, 3, 4], f"应返回自增后的计数序列，实际 {counts}"
        # 键带上 TTL（返回路径顺带确保了窗口）。
        assert 0 < await observer.pttl(key) <= 120 * 1000
    finally:
        await observer.aclose()
        await _cleanup(key)


async def test_incr_with_ttl_的返回值让allow边界正确(real_throttle):
    """端到端印证返回值语义：limit=2 时序列应为 [True, True, False]，边界由返回计数决定。"""
    key = f"test:allow:suppl-boundary:{uuid4().hex}"
    observer = real_throttle

    try:
        results = [
            await throttle.allow(key, limit=2, window_seconds=120) for _ in range(3)
        ]
        assert results == [True, True, False], (
            f"返回计数若不正确，边界会错位，实际 {results}"
        )
    finally:
        await observer.aclose()
        await _cleanup(key)


# ---------------------------------------------------------------------------
# 集成：/api/auth/login 全流程 —— 账号键带 TTL，冷却能自行过期
# ---------------------------------------------------------------------------


async def _seed_user(db_session, email: str):
    """建一个可登录的用户（供 HTTP 集成用例驱动失败 / 成功登录）。"""
    from app.iam.models import User
    from app.iam.permissions import Role
    from app.iam.security import hash_password

    user = User(
        email=email,
        name=email,
        role=Role.GRC_LEAD,
        password_hash=hash_password("pw123456"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def test_登录失败累积后账号键带TTL会自行过期(
    client, db_session, monkeypatch, unique_ids
):
    """端到端：通过 `/api/auth/login` 反复失败后，账号计数键带上 TTL，使锁定自行过期。

    不必等 15 分钟——只断言 0 < PTTL <= 窗口，即「窗口存在且终将过期」。修复前首次
    expire 一旦丢失，键会永久无 TTL、永不过期。这里走正常流程（不注入失败），确认
    happy path 下 HTTP 层出来的键也带 TTL。

    conftest autouse 把 `_client` 换成了内存假 Redis；本用例在其后再 monkeypatch
    指回真实 Redis，以便观测真实 PTTL。
    """
    email = unique_ids["email"]
    await _seed_user(db_session, email)

    async def _client_fn():
        return _real_redis()

    monkeypatch.setattr(throttle, "_client", _client_fn)

    account_key = throttle._keys(email, None)[0]
    observer = _real_redis()
    try:
        body = {"email": email, "password": "wrong"}
        for _ in range(3):
            resp = await client.post("/api/auth/login", json=body)
            assert resp.status_code == 401

        count = await observer.get(account_key)
        assert count is not None and int(count) == 3, (
            f"三次失败应在账号键上计到 3，实际 {count}"
        )
        pttl = await observer.pttl(account_key)
        assert 0 < pttl <= throttle.WINDOW_SECONDS * 1000, (
            f"HTTP 登录失败累积后账号键应带 TTL（窗口会自行过期），实际 PTTL={pttl}"
        )
    finally:
        await observer.aclose()
        # 只清本用例的账号键。测试客户端的 IP（共享的回环地址）可能也被计数，但那是
        # 跨用例共享的键，不在这里删，以免干扰并行/相邻用例；它自带 TTL 会自行过期。
        await _cleanup(account_key)


async def test_成功登录只清账号键_IP键仍带TTL(client, db_session, monkeypatch, unique_ids):
    """集成（per-IP 维度 + clear 作用范围）：成功登录只删账号键，IP 键原封不动且仍带 TTL。

    验证需求 3.3（clear 只清账号键、绝不碰 IP 键）与 2.3（IP 键始终带 TTL、靠窗口自行过期）
    的组合：先在真实 Redis 上经 `record_failure` 攒上账号 + IP 两维度计数（两键都带 TTL），
    再通过 `/api/auth/login` 成功登录触发 `clear`，断言账号键被清、IP 键仍在且仍带 TTL。
    """
    email, ip = unique_ids["email"], unique_ids["ip"]
    await _seed_user(db_session, email)

    async def _client_fn():
        return _real_redis()

    monkeypatch.setattr(throttle, "_client", _client_fn)

    account_key, ip_key = throttle._keys(email, ip)
    observer = _real_redis()
    try:
        # 在账号 + IP 两维度各攒两次失败（两键都应带 TTL）。
        for _ in range(2):
            await throttle.record_failure(email, ip)
        assert int(await observer.get(account_key)) == 2
        assert int(await observer.get(ip_key)) == 2
        ip_pttl_before = await observer.pttl(ip_key)
        assert ip_pttl_before > 0, "前置条件：IP 键应带 TTL"

        # 成功登录 -> 触发 throttle.clear(email)（只清账号键）。
        resp = await client.post(
            "/api/auth/login", json={"email": email, "password": "pw123456"}
        )
        assert resp.status_code == 200

        # 账号键被清。
        assert await observer.get(account_key) is None, "成功登录应清掉账号计数"
        # IP 键仍在、计数不变、且仍带 TTL（靠窗口自行过期，不依赖 clear）。
        assert int(await observer.get(ip_key)) == 2, "IP 计数不该被成功登录抹掉"
        ip_pttl_after = await observer.pttl(ip_key)
        assert ip_pttl_after > 0, (
            f"IP 键应始终带 TTL 以自行过期，clear 后实际 PTTL={ip_pttl_after}"
        )
        assert ip_pttl_after <= throttle.WINDOW_SECONDS * 1000
    finally:
        await observer.aclose()
        await _cleanup(account_key, ip_key)
