from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.clauses.models import EMBEDDING_DIM
from app.controls.models import Control
from app.relations.indexing import embed_pending, render_control
from app.relations.models import ControlEmbedding


def _vectors(count: int):
    return ([[0.02] * EMBEDDING_DIM for _ in range(count)], count)


async def _controls(db_session, how_many: int) -> list[Control]:
    rows = [
        Control(code=f"C-{n:04d}", title=f"Control {n}", statement=f"Statement {n}.")
        for n in range(1, how_many + 1)
    ]
    db_session.add_all(rows)
    await db_session.flush()
    return rows


def test_render_control_embeds_the_statement_alone():
    """code 与 title 一起喂会淹掉信号——实测见 render_control 的 docstring。"""
    from types import SimpleNamespace

    text = render_control(SimpleNamespace(
        code="C-0009", title="Dual approval", statement="Two approvers required."))
    assert text == "Two approvers required."


def test_render_control_falls_back_to_the_title_when_there_is_no_statement():
    from types import SimpleNamespace

    text = render_control(SimpleNamespace(code="C-0009", title="Dual approval", statement=""))
    assert text == "Dual approval"


@pytest.mark.asyncio
async def test_embedding_backfills_every_control_without_one(db_session):
    await _controls(db_session, 3)
    with patch("app.relations.indexing.embed", new=AsyncMock(return_value=_vectors(3))), \
         patch("app.relations.indexing.current_model", new=AsyncMock(return_value="embo-01")):
        result = await embed_pending(db_session)

    assert result["embedded"] == 3
    rows = list(await db_session.scalars(select(ControlEmbedding)))
    assert len(rows) == 3
    assert all(len(r.embedding) == EMBEDDING_DIM for r in rows)
    assert all(r.embedding_model == "embo-01" for r in rows)


@pytest.mark.asyncio
async def test_a_second_run_is_a_no_op(db_session):
    await _controls(db_session, 2)
    with patch("app.relations.indexing.embed", new=AsyncMock(return_value=_vectors(2))), \
         patch("app.relations.indexing.current_model", new=AsyncMock(return_value="embo-01")):
        await embed_pending(db_session)
        again = await embed_pending(db_session)
    assert again["embedded"] == 0


@pytest.mark.asyncio
async def test_changing_the_model_reembeds_everything(db_session):
    await _controls(db_session, 2)
    with patch("app.relations.indexing.embed", new=AsyncMock(return_value=_vectors(2))), \
         patch("app.relations.indexing.current_model", new=AsyncMock(return_value="embo-01")):
        await embed_pending(db_session)
    with patch("app.relations.indexing.embed", new=AsyncMock(return_value=_vectors(2))), \
         patch("app.relations.indexing.current_model", new=AsyncMock(return_value="embo-02")):
        result = await embed_pending(db_session)

    assert result["embedded"] == 2
    rows = list(await db_session.scalars(select(ControlEmbedding)))
    assert len(rows) == 2, "换模型是重算，不是新增行"
    assert all(r.embedding_model == "embo-02" for r in rows)


@pytest.mark.asyncio
async def test_an_empty_library_reports_nothing_to_do(db_session):
    with patch("app.relations.indexing.current_model", new=AsyncMock(return_value="embo-01")):
        result = await embed_pending(db_session)
    assert result["embedded"] == 0 and result["pending"] == 0


@pytest.mark.asyncio
async def test_failure_leaves_earlier_batches_committed(db_session, monkeypatch):
    """逐批落盘，与 indexing/embedder.py 同一条理由。

    这个函数是在 HTTP 请求里跑的：不逐批提交，第 N 批的 ProviderError 会把前
    N-1 批的向量、连同 embed() 在 finally 里写的 LLMCall 合规留痕一起回滚。
    """
    from app.llm.providers.base import ProviderError
    from app.relations import indexing

    monkeypatch.setattr(indexing, "BATCH_SIZE", 2)
    await _controls(db_session, 5)
    calls = {"n": 0}

    async def flaky(session, *, texts):
        calls["n"] += 1
        if calls["n"] > 1:
            raise ProviderError("上游返回 500", retryable=True)
        return _vectors(len(texts))

    with patch("app.relations.indexing.embed", new=AsyncMock(side_effect=flaky)), \
         patch("app.relations.indexing.current_model", new=AsyncMock(return_value="embo-01")), \
         pytest.raises(ProviderError):
        await embed_pending(db_session)

    done = list(await db_session.scalars(
        select(ControlEmbedding).where(ControlEmbedding.embedding.is_not(None))
    ))
    assert len(done) == 2, "第一批已落盘，不该跟着回滚"


@pytest.mark.asyncio
async def test_the_default_limit_is_two_round_trips(db_session):
    """默认值是给 HTTP 请求用的，不是给后台任务用的：剩下的靠再点一次。"""
    from app.relations.indexing import BATCH_SIZE, DEFAULT_LIMIT

    assert DEFAULT_LIMIT == BATCH_SIZE * 2

    await _controls(db_session, DEFAULT_LIMIT + 3)
    with patch("app.relations.indexing.embed",
               new=AsyncMock(side_effect=lambda s, *, texts: _vectors(len(texts)))), \
         patch("app.relations.indexing.current_model", new=AsyncMock(return_value="embo-01")):
        result = await embed_pending(db_session)

    assert result["embedded"] == DEFAULT_LIMIT
    assert result["pending"] == 3, "响应要告诉前端还剩多少"


@pytest.mark.asyncio
async def test_changing_the_render_version_reembeds_even_on_the_same_model(db_session):
    """改 render_control 就是改了向量的含义。

    只按 embedding_model 判 stale 时，换渲染方式后模型名没变，一库出自旧渲染的
    向量会被当成最新的，一条都不重算——静默地把新旧两种向量混在一起比余弦。
    """
    from app.relations import indexing

    await _controls(db_session, 2)
    with patch("app.relations.indexing.embed", new=AsyncMock(return_value=_vectors(2))), \
         patch("app.relations.indexing.current_model", new=AsyncMock(return_value="embo-01")):
        await embed_pending(db_session)
        assert (await embed_pending(db_session))["embedded"] == 0

        monkeyed = "v-next"
        with patch.object(indexing, "EMBEDDING_VERSION", monkeyed):
            again = await embed_pending(db_session)

    assert again["embedded"] == 2, "渲染版本变了就该重算"
    rows = list(await db_session.scalars(select(ControlEmbedding)))
    assert len(rows) == 2, "重算不是新增行"
    assert {r.embedding_version for r in rows} == {"v-next"}
