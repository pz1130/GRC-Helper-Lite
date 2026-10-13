from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.search.expansion import expand, needs_expansion
from app.search.models import QueryExpansionCache


def _result(terms: list[str]):
    from app.llm.runner import ValidatedResult

    return ValidatedResult(payload={"terms": terms}, confidence=None, llm_call_id=1)


def test_pure_english_query_needs_no_expansion():
    assert not needs_expansion("privileged access management")
    assert not needs_expansion("VaultKeeper 2FA")


def test_query_with_chinese_needs_expansion():
    assert needs_expansion("特权访问管理")
    assert needs_expansion("VaultKeeper 的特权账号")


@pytest.mark.asyncio
async def test_english_query_is_returned_untouched_without_calling_the_model(db_session):
    runner = AsyncMock()
    with patch("app.search.expansion.run", new=runner):
        terms = await expand(db_session, "privileged access")
    assert terms == ["privileged access"]
    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_chinese_query_is_expanded_with_english_terms(db_session):
    with patch(
        "app.search.expansion.run",
        new=AsyncMock(return_value=_result(["Privileged Access Management", "PAM"])),
    ):
        terms = await expand(db_session, "特权访问管理")
    assert terms[0] == "特权访问管理"
    assert "Privileged Access Management" in terms


@pytest.mark.asyncio
async def test_expansion_is_cached(db_session):
    runner = AsyncMock(return_value=_result(["Privileged Access Management"]))
    with patch("app.search.expansion.run", new=runner):
        await expand(db_session, "特权访问管理")
        await expand(db_session, "特权访问管理")
    assert runner.await_count == 1
    assert await db_session.scalar(select(QueryExpansionCache)) is not None


@pytest.mark.asyncio
async def test_cached_terms_are_reused_verbatim(db_session):
    with patch(
        "app.search.expansion.run", new=AsyncMock(return_value=_result(["PAM", "vaulting"]))
    ):
        first = await expand(db_session, "特权访问管理")
    with patch("app.search.expansion.run", new=AsyncMock(side_effect=AssertionError("不该再调"))):
        second = await expand(db_session, "特权访问管理")
    assert first == second


@pytest.mark.asyncio
async def test_model_failure_degrades_to_the_original_query(db_session):
    from app.llm.providers.base import ProviderError

    with patch(
        "app.search.expansion.run",
        new=AsyncMock(side_effect=ProviderError("上游 500", retryable=True)),
    ):
        terms = await expand(db_session, "特权访问管理")
    assert terms == ["特权访问管理"]


@pytest.mark.asyncio
async def test_unrouted_task_degrades_instead_of_raising(db_session):
    from app.llm.routing import RoutingError

    with patch("app.search.expansion.run", new=AsyncMock(side_effect=RoutingError("未配置"))):
        assert await expand(db_session, "特权访问管理") == ["特权访问管理"]


@pytest.mark.asyncio
async def test_blank_query_short_circuits(db_session):
    runner = AsyncMock()
    with patch("app.search.expansion.run", new=runner):
        assert await expand(db_session, "  ") == []
    runner.assert_not_awaited()
