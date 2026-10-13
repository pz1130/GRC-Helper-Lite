"""健康检查必须真的查一下数据库。

原来它只返回一个常量——库挂了照样 `ok`。而生产 compose 的 healthcheck 用的就是它，
于是一个连不上库的容器会被报告为健康，编排层既不会重启也不会摘掉它。
那样的 healthcheck 只证明"进程还活着"。

**Redis 不算在内**：登录限流在 Redis 不可用时是放行的（见 iam/throttle.py），
API 照样能服务。把它算进健康判定，会让一次缓存抖动变成整个服务被重启。
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


async def _get():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/health")


@pytest.mark.asyncio
async def test_health_returns_ok_when_the_database_answers():
    resp = await _get()

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"]


@pytest.mark.asyncio
async def test_health_is_503_when_the_database_is_unreachable():
    with patch("app.main.database_reachable", AsyncMock(return_value=False)):
        resp = await _get()

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["database"] == "down"


@pytest.mark.asyncio
async def test_the_body_says_which_check_failed():
    """编排层只看状态码，但值班的人要一眼看出是哪一环。"""
    with patch("app.main.database_reachable", AsyncMock(return_value=False)):
        body = (await _get()).json()

    assert "database" in body
