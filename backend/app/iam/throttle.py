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
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, WINDOW_SECONDS)
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
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_seconds)
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
