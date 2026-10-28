"""throttle-ttl-persistence —— bug 条件探查测试（在修复之前）。

缺陷：计数键的「自增」（`incr`）与「设 TTL」（`expire`）是两条独立命令，且 `expire`
只在首次（`count == 1`）执行。一旦首次 `expire` 遇到瞬时错误（被 `record_failure` /
`allow` 外层宽泛的 `except Exception` 吞掉），这个键就永久停在「有计数、无 TTL」：
之后每次失败 `count != 1`，`expire` 再也不会重试，窗口永不过期。

**本测试在未修复代码上必须 FAIL —— 失败即证明缺陷存在。** 这些用例同时编码了
**期望行为**（Property 1：任何一次计数之后对应键都应携带正的 TTL），修复后它们将转为
PASS，即构成 Fix Checking 的验证。

**测试后端**：现有 `test_login_throttle.py` 的 `_FakeRedis` / conftest 的 `_MemoryRedis`
的 `expire` 都是空操作、不记录 TTL，无法观测 PTTL。本测试改用 Docker 内的真实 Redis
（`throttle.redis_url` -> `get_settings().redis_url`）做 PTTL 断言，并用 monkeypatch
注入「首次 expire 抛错」这一拆分失败场景。注意：Docker 内的 Redis 在一次测试 run 里是
共享的，故每个用例用唯一的 email / IP / key，并在结束时清理自己的键。
"""

from uuid import uuid4

import pytest

from app.config import get_settings
from app.iam import throttle

pytestmark = pytest.mark.asyncio


class _ExpireLosesFirstCall:
    """包一层真实 Redis：让**第一次** `expire` 抛错（模拟 incr 成功、紧随的 expire
    因瞬时错误丢失，被外层宽泛 `except` 吞掉），其余命令全部透传给真实 Redis。

    `incr` / `get` / `pttl` / `expire`（第二次起）/ `delete` / `aclose` 均作用在真实
    Redis 上，因此可以观测真实的 PTTL。
    """

    def __init__(self, real) -> None:
        self._real = real
        self._expire_calls = 0

    async def incr(self, key: str) -> int:
        return await self._real.incr(key)

    async def get(self, key: str):
        return await self._real.get(key)

    async def pttl(self, key: str) -> int:
        return await self._real.pttl(key)

    async def expire(self, key: str, seconds: int):
        self._expire_calls += 1
        if self._expire_calls == 1:
            # 模拟首次设 TTL 丢失：incr 已提交，这条 expire 没落地。
            raise ConnectionError("注入：首次 expire 丢失（瞬时 Redis 错误）")
        return await self._real.expire(key, seconds)

    async def delete(self, *keys: str):
        return await self._real.delete(*keys)

    async def aclose(self) -> None:
        return None


def _real_redis():
    """直连 Docker 内真实 Redis，用于 PTTL 断言。"""
    return throttle.from_url(get_settings().redis_url)


@pytest.fixture
def unique_ids():
    """每个用例一份唯一的 email / IP，避免共享 Redis 上的串扰。"""
    token = uuid4().hex
    return {
        "email": f"ttl-probe-{token}@example.com",
        "ip": f"10.0.0.{token[:8]}",
    }


async def _cleanup(*keys: str) -> None:
    """清掉本用例用过的键，保持测试之间隔离。"""
    real = _real_redis()
    try:
        await real.delete(*keys)
    finally:
        await real.aclose()


async def test_首次_expire_丢失后账号键没有_TTL(monkeypatch, unique_ids):
    """用例 1：注入首次 `expire` 失败后调用 `record_failure`，账号键被 incr 到 1，
    但 TTL 丢失 —— 未修复代码上 PTTL < 0。

    断言编码期望行为：计数路径结束后账号键 **应当** 携带正的 TTL（PTTL > 0）。
    未修复代码在此失败，暴露缺陷。
    """
    email, ip = unique_ids["email"], unique_ids["ip"]
    account_key = throttle._keys(email, ip)[0]

    real = _real_redis()
    proxy = _ExpireLosesFirstCall(real)

    async def _fake_client():
        return proxy

    monkeypatch.setattr(throttle, "_client", _fake_client)

    try:
        await throttle.record_failure(email, ip)

        pttl = await real.pttl(account_key)
        # 期望行为（Property 1）：键应带正的 TTL，窗口才能自行过期。
        assert pttl > 0, (
            f"账号键 {account_key} 被计数后却没有 TTL（PTTL={pttl}）—— "
            "窗口永不过期（首次 expire 丢失后无补设路径）"
        )
    finally:
        await real.aclose()
        await _cleanup(*throttle._keys(email, ip))


async def test_无TTL键上的后续失败仍然没有_TTL(monkeypatch, unique_ids):
    """用例 2：承用例 1 的无 TTL 键，再记一次失败。`count != 1`，故 `expire` 不再执行 ——
    未修复代码上 PTTL 仍 < 0。

    断言编码期望行为：后续任一次计数都 **应当**（重新）补回 TTL（PTTL > 0）。
    未修复代码在此失败。
    """
    email, ip = unique_ids["email"], unique_ids["ip"]
    account_key = throttle._keys(email, ip)[0]

    real = _real_redis()
    proxy = _ExpireLosesFirstCall(real)

    async def _fake_client():
        return proxy

    monkeypatch.setattr(throttle, "_client", _fake_client)

    try:
        # 第一次失败：首次 expire 被注入丢失 -> 账号键 {value:1, ttl:none}
        await throttle.record_failure(email, ip)
        # 第二次失败：count 变为 2，未修复代码 count != 1 不再 expire
        await throttle.record_failure(email, ip)

        pttl = await real.pttl(account_key)
        count = await real.get(account_key)
        assert pttl > 0, (
            f"账号键 {account_key} 计数已到 {count} 却仍无 TTL（PTTL={pttl}）—— "
            "后续失败未补设 TTL，计数只升不降"
        )
    finally:
        await real.aclose()
        await _cleanup(*throttle._keys(email, ip))


async def test_IP键丢TTL后下次失败应补回TTL(monkeypatch, unique_ids):
    """用例 3：对 per-IP 键制造无 TTL 状态，再在同一 IP 上记一次后续失败
    （`record_failure`）—— 未修复代码上 `incr` 把计数推到 2，`count != 1` 故 `expire`
    不再执行，IP 键仍无 TTL（永久锁死，无恢复路径）。

    断言编码期望行为：IP 键 **应当** 靠窗口自行过期解锁，无需依赖 `clear`
    （按需求 3.3，`clear` 只删账号键、从不碰 IP 键）。恢复路径在 `_incr_with_ttl`：
    按需求 2.2 / 2.3，同一 IP 上的下一次失败会在键缺 TTL 时（重新）补回 TTL。
    修复后该次 `record_failure` 把 TTL 补回，键 PTTL > 0；未修复代码在此失败。

    为了让「有计数、无 TTL」精确落在 IP 键上，这里直接用真实 Redis 预先 `incr`
    出一个无 TTL 的 IP 键（等价于 incr 成功、expire 丢失的中间状态），再走一次
    `record_failure`。注意：`record_failure` 经 `throttle._client` 路由到**同一个**
    真实 Redis，观测连接读到的正是它写入的那个键。
    """
    email, ip = unique_ids["email"], unique_ids["ip"]
    ip_key = throttle._keys(email, ip)[1]

    real = _real_redis()

    async def _fake_client():
        # record_failure 走真实 Redis（与观测连接是同一个 Docker Redis）
        return _real_redis()

    try:
        # 造出「有计数、无 TTL」的 IP 键：等价于 incr 成功但 expire 丢失
        await real.incr(ip_key)
        assert await real.pttl(ip_key) < 0, "前置条件：IP 键此刻应无 TTL"

        monkeypatch.setattr(throttle, "_client", _fake_client)
        # 同一 IP 上的下一次失败：修复后应在键缺 TTL 时补回 TTL（需求 2.2 / 2.3）。
        # 未修复代码把计数推到 2，count != 1 不再 expire -> 仍无 TTL。
        await throttle.record_failure(email, ip)

        pttl = await real.pttl(ip_key)
        count = await real.get(ip_key)
        # 期望行为：下一次失败把 TTL 补回，IP 键靠窗口自行过期解锁。
        assert pttl > 0, (
            f"IP 键 {ip_key} 计数 {count} 却无 TTL（PTTL={pttl}）—— "
            "后续失败未补回 TTL，没有恢复路径，整个 IP 永久锁死"
        )
    finally:
        await real.aclose()
        await _cleanup(*throttle._keys(email, ip))


async def test_allow_同样受首次expire丢失影响(monkeypatch):
    """用例 4（边界）：`allow` 与 `record_failure` 共用同一 incr-then-conditional-expire
    模式。对 `allow` 的键注入首次 expire 丢失 —— 未修复代码上该键 PTTL < 0。

    断言编码期望行为：`allow` 计数后键 **应当** 带正的 TTL（PTTL > 0）。未修复代码在此失败。
    """
    key = f"test:allow:ttl-probe:{uuid4().hex}"

    real = _real_redis()
    proxy = _ExpireLosesFirstCall(real)

    async def _fake_client():
        return proxy

    monkeypatch.setattr(throttle, "_client", _fake_client)

    try:
        # 本窗口内第一次调用：incr 到 1，首次 expire 被注入丢失
        allowed = await throttle.allow(key, limit=3, window_seconds=60)
        assert allowed is True, "第一次调用仍应放行（计数 1 <= limit 3）"

        pttl = await real.pttl(key)
        count = await real.get(key)
        assert pttl > 0, (
            f"allow 的键 {key} 计数 {count} 却无 TTL（PTTL={pttl}）—— "
            "与 record_failure 共用同一缺陷模式，窗口永不过期"
        )
    finally:
        await real.aclose()
        await _cleanup(key)
