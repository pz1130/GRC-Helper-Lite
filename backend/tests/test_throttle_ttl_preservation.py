"""throttle-ttl-persistence —— 保持性属性测试（在修复之前）。

methodology：observation-first。先在**未修复**代码上观察「非 bug 输入」的真实行为，
再把它固化成断言，确保后续修复不引入回归（Property 2：bug 条件不成立的输入行为不变）。

锁定的既有行为（来自 design 的 Preservation Requirements，需求 3.1–3.5）：

- 3.1 / 3.5 固定窗口不被重复命中延长：带有效 TTL 的键在后续命中后 TTL 只自然递减、
  绝不跳回满窗口（固定窗口，非滑动窗口）。用 Docker 内真实 Redis 观测真实 PTTL。
- 3.2 Redis 不可用时 fail-open：`_client` 抛错时 `record_failure` / `allow` / `check`
  都不阻断（allow 返回 True、check 不抛）。用 monkeypatch 让 `_client` 抛错。
- 3.3 `clear` 只清账号键、绝不碰 IP 键。
- 3.4 阈值与两维度不变：MAX_ACCOUNT_FAILURES=5、MAX_IP_FAILURES=50、
  WINDOW_SECONDS=15*60，账号 + IP 两键计数。
- `allow` 窗口内返回语义：limit=3 时 [True, True, True, False, False]。

**本测试在未修复代码上必须全部 PASS —— 确立需要保持的基线行为。**

**测试后端**：TTL 相关断言（3.1/3.5）用 Docker 内真实 Redis（`throttle.redis_url`
-> `get_settings().redis_url`），唯一键 + 结束清理。非 TTL 行为（fail-open / clear /
阈值 / 两维度 / allow 返回序列）沿用现有假 Redis 风格（`_FakeRedis` 不记录 TTL，
对这些项够用）。conftest 的 autouse `_isolated_login_throttle` 会先把 `_client` 换成
内存假 Redis；需要真实 Redis 的用例在其之后再打一次 monkeypatch 覆盖它。
"""

import random
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.config import get_settings
from app.iam import throttle

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 真实 Redis 辅助（TTL 观测）
# ---------------------------------------------------------------------------


def _real_redis():
    """直连 Docker 内真实 Redis，用于 PTTL 断言（与 Task 1 同一后端）。"""
    return throttle.from_url(get_settings().redis_url)


@pytest.fixture
def real_throttle(monkeypatch):
    """把 `throttle._client` 指回真实 Redis，覆盖 conftest 的内存假 Redis。

    返回一个「直连真实 Redis 的观测连接」给用例做 PTTL/GET 断言；用例结束负责清键。
    """
    async def _client():
        return _real_redis()

    monkeypatch.setattr(throttle, "_client", _client)
    observer = _real_redis()
    yield observer


async def _cleanup(*keys: str) -> None:
    real = _real_redis()
    try:
        await real.delete(*keys)
    finally:
        await real.aclose()


@pytest.fixture
def unique_ids():
    token = uuid4().hex
    return {
        "email": f"ttl-preserve-{token}@example.com",
        "ip": f"10.9.{int(token[:2], 16)}.{int(token[2:4], 16)}",
    }


# ---------------------------------------------------------------------------
# 3.1 / 3.5 固定窗口不被重复命中延长（真实 Redis 观测 PTTL）
# ---------------------------------------------------------------------------

# 允许的「向上」容差（毫秒）：PTTL 两次读取之间只应自然递减；给极小的时钟/取整抖动
# 留一点余量，但远小于一个完整窗口，足以把「跳回满窗口」这种延长行为挡在外面。
_TTL_UP_TOLERANCE_MS = 50


async def test_固定窗口在后续命中后不被延长(real_throttle, unique_ids):
    """观察并固化：账号键首次失败带上满窗口 TTL 后，后续命中只自增计数、
    TTL 只自然递减，绝不跳回满窗口（固定窗口，而非滑动窗口）。
    """
    email, ip = unique_ids["email"], unique_ids["ip"]
    account_key = throttle._keys(email, ip)[0]
    observer = real_throttle

    try:
        await throttle.record_failure(email, ip)
        pttl_1 = await observer.pttl(account_key)
        count_1 = int(await observer.get(account_key))
        assert count_1 == 1
        # 首次应落在一个完整窗口内（给调度/取整留极小余量）。
        assert 0 < pttl_1 <= throttle.WINDOW_SECONDS * 1000

        # 连打几次：每次只应自增计数，TTL 不得向上跳（不得被延长）。
        last_pttl = pttl_1
        for expected_count in range(2, 6):
            await throttle.record_failure(email, ip)
            pttl_n = await observer.pttl(account_key)
            count_n = int(await observer.get(account_key))
            assert count_n == expected_count, "计数应单调递增"
            assert pttl_n <= last_pttl + _TTL_UP_TOLERANCE_MS, (
                f"TTL 被延长了：第 {expected_count} 次命中后 PTTL={pttl_n} "
                f"高于上一次 {last_pttl}（固定窗口不该被重设）"
            )
            # 也不该跳回「满窗口」——这是滑动窗口才会出现的行为。
            assert pttl_n <= pttl_1 + _TTL_UP_TOLERANCE_MS, (
                f"TTL 跳回满窗口：PTTL={pttl_n} 接近首次 {pttl_1}（疑似滑动窗口）"
            )
            last_pttl = pttl_n
    finally:
        await observer.aclose()
        await _cleanup(*throttle._keys(email, ip))


async def test_allow_固定窗口在后续命中后不被延长(real_throttle):
    """观察并固化：`allow` 的键同样是固定窗口——首次带 TTL，后续命中只自增、不延长。"""
    key = f"test:allow:preserve:{uuid4().hex}"
    observer = real_throttle

    try:
        await throttle.allow(key, limit=3, window_seconds=120)
        pttl_1 = await observer.pttl(key)
        assert 0 < pttl_1 <= 120 * 1000

        last_pttl = pttl_1
        for _ in range(4):
            await throttle.allow(key, limit=3, window_seconds=120)
            pttl_n = await observer.pttl(key)
            assert pttl_n <= last_pttl + _TTL_UP_TOLERANCE_MS, (
                f"allow 的固定窗口被延长：PTTL={pttl_n} 高于上一次 {last_pttl}"
            )
            last_pttl = pttl_n
    finally:
        await observer.aclose()
        await _cleanup(key)


@pytest.mark.parametrize("seed", [1, 7, 42, 2024])
async def test_随机命中序列下固定窗口始终不被延长(real_throttle, unique_ids, seed):
    """for-all 风格：随机长度的连续命中序列下，TTL 始终单调不增（允许自然递减），
    计数始终单调递增——覆盖「非 bug 输入」里带有效 TTL 的后续命中。
    """
    rng = random.Random(seed)
    email, ip = unique_ids["email"], unique_ids["ip"]
    account_key = throttle._keys(email, ip)[0]
    observer = real_throttle

    hits = rng.randint(3, 8)
    try:
        await throttle.record_failure(email, ip)
        last_pttl = await observer.pttl(account_key)
        assert last_pttl > 0
        for i in range(2, hits + 1):
            await throttle.record_failure(email, ip)
            pttl_n = await observer.pttl(account_key)
            count_n = int(await observer.get(account_key))
            assert count_n == i, "计数单调递增"
            assert pttl_n <= last_pttl + _TTL_UP_TOLERANCE_MS, (
                f"seed={seed} 第 {i} 次命中延长了 TTL：{pttl_n} > {last_pttl}"
            )
            last_pttl = pttl_n
    finally:
        await observer.aclose()
        await _cleanup(*throttle._keys(email, ip))


# ---------------------------------------------------------------------------
# 3.2 Redis 不可用时 fail-open（monkeypatch 让 _client 抛错）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [OSError("redis down"), ConnectionError("refused"), RuntimeError("boom")],
)
async def test_redis不可用时record_failure不抛错(monkeypatch, unique_ids, exc):
    """`_client` 抛错时 `record_failure` 必须静默放行（不抛出、不阻断登录流程）。"""
    monkeypatch.setattr(throttle, "_client", AsyncMock(side_effect=exc))
    # 不抛即为通过。
    await throttle.record_failure(unique_ids["email"], unique_ids["ip"])


@pytest.mark.parametrize(
    "exc",
    [OSError("redis down"), ConnectionError("refused"), RuntimeError("boom")],
)
async def test_redis不可用时check不抛错也不阻断(monkeypatch, unique_ids, exc):
    """`_client` 抛错时 `check` 必须直接返回（放行），绝不抛 TooManyAttempts。"""
    monkeypatch.setattr(throttle, "_client", AsyncMock(side_effect=exc))
    # 不抛即为放行。
    assert await throttle.check(unique_ids["email"], unique_ids["ip"]) is None


@pytest.mark.parametrize(
    "exc",
    [OSError("redis down"), ConnectionError("refused"), RuntimeError("boom")],
)
async def test_redis不可用时allow放行(monkeypatch, exc):
    """`_client` 抛错时 `allow` 必须返回 True（fail-open）。"""
    monkeypatch.setattr(throttle, "_client", AsyncMock(side_effect=exc))
    assert await throttle.allow(f"k:{uuid4().hex}", limit=3, window_seconds=60) is True


async def test_redis不可用时clear不抛错(monkeypatch, unique_ids):
    """`_client` 抛错时 `clear` 也要静默返回，不把成功登录流程带崩。"""
    monkeypatch.setattr(throttle, "_client", AsyncMock(side_effect=OSError("down")))
    await throttle.clear(unique_ids["email"])


# ---------------------------------------------------------------------------
# 3.3 clear 只清账号键、从不碰 IP 键
# ---------------------------------------------------------------------------


class _SpyRedis:
    """记录 delete 收到的键，用于断言 clear 的作用范围。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.deleted: list[str] = []

    async def get(self, key: str):
        return self.values.get(key)

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.deleted.append(key)
            self.values.pop(key, None)

    async def aclose(self) -> None:
        return None


async def test_clear只删账号键保留IP键(monkeypatch, unique_ids):
    """观察并固化：成功登录只删账号键，per-IP 键原封不动（防「成功登录抹掉 IP 计数」绕过）。"""
    email, ip = unique_ids["email"], unique_ids["ip"]
    account_key, ip_key = throttle._keys(email, ip)

    spy = _SpyRedis()
    monkeypatch.setattr(throttle, "_client", AsyncMock(return_value=spy))

    # 先在账号 + IP 两维度都攒上计数。
    await throttle.record_failure(email, ip)
    await throttle.record_failure(email, ip)
    assert spy.values[account_key] == 2
    assert spy.values[ip_key] == 2

    await throttle.clear(email)

    # 账号键被删、IP 键保留。
    assert account_key in spy.deleted, "账号键应被 clear 删除"
    assert ip_key not in spy.deleted, "IP 键绝不该被 clear 碰到"
    assert account_key not in spy.values, "账号计数应清零"
    assert spy.values.get(ip_key) == 2, "IP 计数应保留"


# ---------------------------------------------------------------------------
# 3.4 阈值与两维度计数不变
# ---------------------------------------------------------------------------


class _CountingRedis:
    """内存假 Redis：get / incr / expire / delete，够 check + record_failure 用。"""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def get(self, key: str):
        v = self.values.get(key)
        return None if v is None else str(v)

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


async def test_阈值常量不变():
    """观察并固化阈值常量：5 / 50 / 15min。"""
    assert throttle.MAX_ACCOUNT_FAILURES == 5
    assert throttle.MAX_IP_FAILURES == 50
    assert throttle.WINDOW_SECONDS == 15 * 60


async def test_两维度都被计数(monkeypatch, unique_ids):
    """观察并固化：一次失败同时在账号键与 IP 键各自 +1（两维度计数）。"""
    email, ip = unique_ids["email"], unique_ids["ip"]
    account_key, ip_key = throttle._keys(email, ip)

    fake = _CountingRedis()
    monkeypatch.setattr(throttle, "_client", AsyncMock(return_value=fake))

    for _ in range(3):
        await throttle.record_failure(email, ip)

    assert fake.values[account_key] == 3
    assert fake.values[ip_key] == 3


async def test_账号维度在第6次失败时被挡(monkeypatch, unique_ids):
    """账号到达 MAX_ACCOUNT_FAILURES=5 后，下一次 check 抛 429；此前放行。"""
    email, ip = unique_ids["email"], unique_ids["ip"]
    fake = _CountingRedis()
    monkeypatch.setattr(throttle, "_client", AsyncMock(return_value=fake))

    for _ in range(throttle.MAX_ACCOUNT_FAILURES):
        # 到达上限前 check 都应放行
        assert await throttle.check(email, ip) is None
        await throttle.record_failure(email, ip)

    with pytest.raises(throttle.TooManyAttempts):
        await throttle.check(email, ip)


async def test_IP维度在第51次失败时被挡而账号维度未到(monkeypatch):
    """IP 到达 MAX_IP_FAILURES=50 后 check 抛 429，验证 IP 维度独立于账号维度生效。

    每次失败换一个不同账号（都 < 账号阈值），但共用同一个 IP，攒满 IP 计数。
    """
    ip = f"10.9.{random.randint(1, 254)}.{random.randint(1, 254)}"
    fake = _CountingRedis()
    monkeypatch.setattr(throttle, "_client", AsyncMock(return_value=fake))

    for i in range(throttle.MAX_IP_FAILURES):
        email = f"ip-dim-{i}-{uuid4().hex}@example.com"
        # 换新账号，账号维度永远到不了阈值；但 IP 维度在累加。
        assert await throttle.check(email, ip) is None
        await throttle.record_failure(email, ip)

    # 第 51 次：某个全新账号（账号维度计数为 0），仅因 IP 维度已满就被挡。
    fresh_email = f"ip-dim-last-{uuid4().hex}@example.com"
    with pytest.raises(throttle.TooManyAttempts):
        await throttle.check(fresh_email, ip)


async def test_无IP时只按账号计一维(monkeypatch, unique_ids):
    """ip 为 None 时只有账号键一个维度（_keys 不产出 IP 键）。"""
    email = unique_ids["email"]
    fake = _CountingRedis()
    monkeypatch.setattr(throttle, "_client", AsyncMock(return_value=fake))

    await throttle.record_failure(email, None)

    keys = throttle._keys(email, None)
    assert len(keys) == 1
    assert fake.values[keys[0]] == 1
    assert not any(k.startswith("login:fail:ip:") for k in fake.values)


# ---------------------------------------------------------------------------
# allow 窗口内返回语义：第 limit 次以内 True，之后 False
# ---------------------------------------------------------------------------


async def test_allow返回序列_limit3(monkeypatch):
    """观察并固化：limit=3 窗口内连续 5 次 -> [True, True, True, False, False]。"""
    fake = _CountingRedis()
    monkeypatch.setattr(throttle, "_client", AsyncMock(return_value=fake))

    key = f"test:allow:seq:{uuid4().hex}"
    results = [
        await throttle.allow(key, limit=3, window_seconds=60) for _ in range(5)
    ]
    assert results == [True, True, True, False, False]


@pytest.mark.parametrize("limit", [1, 2, 5, 10])
async def test_allow返回序列_任意limit(monkeypatch, limit):
    """for-all 风格：任意 limit 下，前 limit 次 True、其后 False。"""
    fake = _CountingRedis()
    monkeypatch.setattr(throttle, "_client", AsyncMock(return_value=fake))

    key = f"test:allow:seq:{uuid4().hex}"
    total = limit + 3
    results = [
        await throttle.allow(key, limit=limit, window_seconds=60)
        for _ in range(total)
    ]
    assert results == [True] * limit + [False] * (total - limit)
