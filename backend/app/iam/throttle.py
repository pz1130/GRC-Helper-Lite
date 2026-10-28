"""登录失败限流。

全仓原先没有任何 rate limit——密码可以无限次猜。内网自用还能忍，一旦按 OPERATIONS.md
说的部署到公网就不行。

**两个维度都计**：按账号挡的是针对某个人的爆破；按 IP 挡的是拿一份邮箱列表扫。
IP 的阈值高得多，因为办公网出口是一个 IP，一屋子人共用。

**计数放 Redis**：天然带过期、不需要迁移，重启丢了也只是把冷却提前结束。

**Redis 不可用时放行。** 这是个明确的取舍：GRC 系统被自己的缓存故障锁死，
比"限流暂时失效"糟得多。代价是攻击者若能打垮 Redis 就能绕过限流——
但那种情形下他已经在网络里了，限流不是当务之急。
"""

import logging
from typing import Any

from redis.asyncio import from_url

from app.config import get_settings
from app.errors import AppError

logger = logging.getLogger(__name__)

# 同一账号 15 分钟内错这么多次就冷却。5 次足够容下"记错了密码"，
# 又让在线爆破慢到没有意义。
MAX_ACCOUNT_FAILURES = 5
# 同一 IP 的阈值高得多：办公网出口是一个 IP，一屋子人共用。
MAX_IP_FAILURES = 50
WINDOW_SECONDS = 15 * 60


class TooManyAttempts(AppError):
    status_code = 429
    code = "auth.too_many_attempts"


# 「自增 + 幂等确保 TTL」的单一来源：服务端原子执行的 Lua 脚本。
#
# 原先的缺陷是把「自增」与「设 TTL」拆成两条命令，且只在 count==1 时 expire。
# 一旦首次 expire 遇到瞬时错误（被外层宽泛 except 吞掉），键就永久停在「有计数、
# 无 TTL」，之后 count != 1 永不补设，窗口永不过期。
#
# 这段脚本在服务端一次性完成：先 INCR，再在键没有 TTL 时（PTTL < 0，
# -1 表示无过期、-2 表示键不存在）才补 EXPIRE，最后返回计数值。
#   - 原子：不存在「incr 成功、expire 丢失」的中间窗口；
#   - 幂等：任何一次调用都能在键缺 TTL 时补回 TTL；
#   - 固定窗口：只在「无 TTL」时补设、从不覆盖已有 TTL，窗口始终锚定在第一次失败，
#     后续命中只自增计数、不延长 TTL（不是滑动窗口）。
_INCR_WITH_TTL_LUA = """
-- KEYS[1] = 计数键; ARGV[1] = 窗口秒数
local n = redis.call('INCR', KEYS[1])
if redis.call('PTTL', KEYS[1]) < 0 then   -- -1 无过期, -2 不存在
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return n
"""


async def _incr_with_ttl(redis: Any, key: str, window_seconds: int) -> int:
    """原子地自增计数并在键缺 TTL 时补设 TTL，返回自增后的计数值。

    首选路径（真实 Redis）：用服务端 Lua 脚本一次性完成 INCR + 条件 EXPIRE。脚本仅在
    键没有有效 TTL（PTTL < 0）时才补 EXPIRE，**永不覆盖已有 TTL**——窗口始终锚定在第一次
    失败（固定窗口，非滑动窗口）。因为在服务端原子执行，不存在「incr 成功、expire 丢失」
    的中间状态，缺陷无从产生；即便某次 TTL 曾经丢失，后续任一次调用也会把它补回来。
    脚本通过传入的 `redis` 对象执行（优先 `register_script` 生成的可调用对象，其次
    `redis.eval`），因此始终作用在调用方 `_client()` 返回的那个连接上。

    回退路径（不支持脚本的连接，如测试替身）：退化为 Python 侧的「INCR + 幂等确保 TTL」。
    仍然遵守同一不变式——只在键缺 TTL 时补设、不覆盖已有 TTL；并对「补设 TTL 这一步
    瞬时失败」做一次补偿重试，从而消除原缺陷里「首次 expire 丢失后再无补设路径」的缺口。
    """
    register = getattr(redis, "register_script", None)
    if register is not None:
        script = register(_INCR_WITH_TTL_LUA)
        count = await script(keys=[key], args=[window_seconds], client=redis)
        return int(count)

    evaluate = getattr(redis, "eval", None)
    if evaluate is not None:
        count = await evaluate(_INCR_WITH_TTL_LUA, 1, key, window_seconds)
        return int(count)

    # 回退：连接不支持脚本。保持与 Lua 相同的语义——仅在键缺 TTL 时补设。
    count = await redis.incr(key)
    await _ensure_ttl(redis, key, window_seconds)
    return int(count)


async def _ensure_ttl(redis: Any, key: str, window_seconds: int) -> None:
    """回退路径下在键缺 TTL 时补设 TTL（固定窗口，不覆盖已有 TTL）。

    若连接能查 PTTL，则只在 `PTTL < 0`（无过期/刚被 incr 创建）时才 EXPIRE，天然不延长
    已有窗口；并对「首次 EXPIRE 瞬时失败」做一次补偿重试，关掉原缺陷「TTL 丢了就永不补」
    的缺口。若连接不支持 PTTL（最简易的测试替身），退化为尽力而为地 EXPIRE 一次。
    """
    pttl = getattr(redis, "pttl", None)
    if pttl is None:
        # 最简易替身（无 PTTL）：沿用原始的尽力而为 expire，计数语义不变。
        try:
            await redis.expire(key, window_seconds)
        except Exception:  # noqa: BLE001 —— 设 TTL 失败不应阻断计数
            logger.warning("设置限流窗口 TTL 失败: %s", key)
        return

    # 支持 PTTL：固定窗口——仅在缺 TTL 时补设，并对瞬时失败补偿重试一次。
    if int(await pttl(key)) >= 0:
        return
    try:
        await redis.expire(key, window_seconds)
    except Exception:  # noqa: BLE001 —— 首次设 TTL 瞬时失败，下面补偿重试
        logger.warning("设置限流窗口 TTL 首次失败，重试补设: %s", key)
        if int(await pttl(key)) < 0:
            await redis.expire(key, window_seconds)


async def _client() -> Any:
    return from_url(get_settings().redis_url)


def _keys(email: str, ip: str | None) -> list[str]:
    keys = [f"login:fail:account:{email.strip().casefold()}"]
    if ip:
        keys.append(f"login:fail:ip:{ip}")
    return keys


async def check(email: str, ip: str | None) -> None:
    """已被冷却就抛 429。Redis 不可用时静默放行。"""
    try:
        redis = await _client()
    except Exception:  # noqa: BLE001 —— 见模块文档：宁可放行也不锁死
        logger.warning("登录限流不可用（Redis 连不上），本次放行")
        return
    try:
        account_key, *ip_keys = _keys(email, ip)
        limits = [(account_key, MAX_ACCOUNT_FAILURES)]
        limits += [(key, MAX_IP_FAILURES) for key in ip_keys]
        for key, ceiling in limits:
            current = await redis.get(key)
            if current is not None and int(current) >= ceiling:
                raise TooManyAttempts(
                    "登录尝试过于频繁，请稍后再试",
                    code="auth.too_many_attempts",
                )
    except TooManyAttempts:
        raise
    except Exception:  # noqa: BLE001
        logger.warning("登录限流读取失败，本次放行")
    finally:
        await _close(redis)


async def record_failure(email: str, ip: str | None) -> None:
    try:
        redis = await _client()
    except Exception:  # noqa: BLE001
        return
    try:
        for key in _keys(email, ip):
            await _incr_with_ttl(redis, key, WINDOW_SECONDS)
    except Exception:  # noqa: BLE001
        logger.warning("登录失败计数写入失败")
    finally:
        await _close(redis)


async def clear(email: str) -> None:
    """登录成功后只清这个账号的计数——**不碰 IP 计数**。

    原先两个一起清，于是持有任意一个有效账号的人可以：错 49 次 → 用自己的账号
    成功登录一次 → IP 计数归零 → 再来，无限循环。按 IP 限流唯一想防的就是
    "拿一份邮箱列表扫"，而这条路对它完全失效。

    办公网共用出口的顾虑由窗口兜着：计数从第一次失败起算 TTL（`record_failure`
    里 `count == 1` 才 expire），到点自然过期，不需要靠成功登录来解锁。
    """
    try:
        redis = await _client()
    except Exception:  # noqa: BLE001
        return
    try:
        await redis.delete(_keys(email, None)[0])
    except Exception:  # noqa: BLE001
        logger.warning("登录失败计数清理失败")
    finally:
        await _close(redis)


async def allow(key: str, *, limit: int, window_seconds: int) -> bool:
    """通用固定窗口计数：本窗口内第 `limit` 次以内返回 True，之后返回 False。

    给登录以外的"按用户限频"用（如搜索的查询扩展）。与登录限流同一取舍：
    Redis 不可用时放行——调用方仍有预算闸门兜底。
    """
    try:
        redis = await _client()
    except Exception:  # noqa: BLE001
        logger.warning("限流不可用（Redis 连不上），本次放行: %s", key)
        return True
    try:
        count = await _incr_with_ttl(redis, key, window_seconds)
        return int(count) <= limit
    except Exception:  # noqa: BLE001
        logger.warning("限流计数失败，本次放行: %s", key)
        return True
    finally:
        await _close(redis)


async def _close(redis: Any) -> None:
    close = getattr(redis, "aclose", None) or getattr(redis, "close", None)
    if close is not None:
        await close()
