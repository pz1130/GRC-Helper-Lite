"""纯编排测试：不连数据库，也不用共享 fixture。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.conflicts import tasks
from app.conflicts.clustering import ClauseRef
from app.llm.providers.base import ProviderError
from app.llm.runner import ValidatedResult
from app.llm.validation import ValidationFailure
from app.relations.clustering import Pair
from app.worker import LONG_JOB_TIMEOUT, WorkerSettings


def conflict(**overrides):
    return {
        "clause_a_id": 91, "clause_b_id": 92,
        "topic": "密码轮换周期", "difference": "一处 90 天，一处 180 天",
        "quote_a": "every 90 days", "quote_b": "every 180 days",
        "confidence": 0.8, **overrides,
    }


@pytest.fixture
def harness(monkeypatch):
    call = SimpleNamespace(id=10, redaction_hits={"dictionary": 2})
    session = MagicMock()

    async def get(model, key):
        from app.llm.models import AppSetting

        return None if model is AppSetting else call

    session.get = AsyncMock(side_effect=get)
    session.scalars = AsyncMock(return_value=[])
    session.scalar = AsyncMock(return_value=None)   # 无检查点
    # 假会话答不了路由查询；检查点指纹里的模型名固定下来。
    monkeypatch.setattr(tasks, "routed_model", AsyncMock(return_value="model-a"))
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()

    @asynccontextmanager
    async def savepoint():
        yield None

    session.begin_nested = savepoint

    bundles = {
        1: [ClauseRef(91, "Password Policy", "4.2", "Passwords rotate every 90 days.")],
        2: [ClauseRef(92, "Access Standard", "7.1", "Passwords rotate every 180 days.")],
    }
    embed = AsyncMock(return_value={"embedded": 0})
    monkeypatch.setattr(tasks, "embed_pending", embed)
    monkeypatch.setattr(tasks, "cross_document_pairs", AsyncMock(return_value=[Pair(1, 2, 0.9)]))
    monkeypatch.setattr(tasks, "clause_bundles", AsyncMock(return_value=bundles))
    run = AsyncMock(return_value=ValidatedResult({"conflicts": [conflict()]}, 0.9, 10))
    create = AsyncMock(side_effect=lambda *a, **k: SimpleNamespace(id=21))
    monkeypatch.setattr(tasks, "run", run)
    monkeypatch.setattr(tasks.review_service, "create", create, raising=False)
    monkeypatch.setattr(
        tasks.ConflictCitationValidator, "check", AsyncMock(return_value=None), raising=False)
    return SimpleNamespace(session=session, call=call, run=run, create=create, embed=embed)


async def test_vectors_are_topped_up_before_any_candidate_is_generated(harness):
    # 146 个控制点里曾有 10 个从未被 embed，第一级粗筛完全看不见它们（OQ-12）。
    await tasks.run_detection(harness.session, run_key="t1")

    harness.embed.assert_awaited()


async def test_one_batch_produces_one_proposal(harness):
    summary = await tasks.run_detection(harness.session, run_key="t1")

    assert summary["batches"] == 1
    assert summary["proposals"] == 1
    assert summary["proposal_ids"] == [21]


async def test_acceptance_sample_limits_provider_calls(harness, monkeypatch):
    pairs = [Pair(index, index + 100, 0.9) for index in range(1, 4)]
    monkeypatch.setattr(tasks, "_build_batches", AsyncMock(return_value=[
        tasks.Batch("system", f"prompt {index}", [91, 92], (frozenset((91, 92)),))
        for index, _ in enumerate(pairs)
    ]))

    summary = await tasks.run_detection(harness.session, run_key="t1", max_batches=2)

    assert summary["batches"] == 2
    assert summary["total_batches"] == 3
    assert summary["limited"] is True
    assert harness.run.await_count == 2


async def test_the_proposal_is_filed_under_the_conflict_kind(harness):
    from app.review.models import ProposalKind

    await tasks.run_detection(harness.session, run_key="t1")

    assert harness.create.await_args.kwargs["kind"] is ProposalKind.CONFLICT


async def test_a_schema_failure_rejects_the_batch_without_killing_the_run(harness):
    harness.run.side_effect = ValidationFailure("bad json")

    summary = await tasks.run_detection(harness.session, run_key="t1")

    assert summary["rejected"] == 1 and summary["proposals"] == 0


async def test_a_provider_failure_is_counted_and_the_run_continues(harness):
    harness.run.side_effect = ProviderError("upstream down", retryable=True)

    summary = await tasks.run_detection(harness.session, run_key="t1")

    assert summary["failed"] == 1 and summary["proposals"] == 0


async def test_a_bad_citation_drops_only_that_conflict(harness, monkeypatch):
    harness.run.return_value = ValidatedResult(
        {"conflicts": [conflict(), conflict(topic="审批权限")]}, 0.9, 10)

    async def check(self, payload):
        entries = payload.get("conflicts", [])
        # 整批查说有问题；逐条查时只有「审批权限」那条不合格。
        if len(entries) != 1:
            return "整批有问题"
        return None if entries[0]["topic"] == "密码轮换周期" else "引文找不到"

    monkeypatch.setattr(tasks.ConflictCitationValidator, "check", check, raising=False)

    summary = await tasks.run_detection(harness.session, run_key="t1")

    assert summary["dropped_conflicts"] == 1
    assert summary["proposals"] == 1


async def test_the_same_conflict_is_not_proposed_twice_in_one_run(harness):
    harness.run.return_value = ValidatedResult(
        {"conflicts": [conflict(), conflict(clause_a_id=92, clause_b_id=91)]}, 0.9, 10)

    summary = await tasks.run_detection(harness.session, run_key="t1")

    # 冲突无方向：(91, 92) 与 (92, 91) 是同一条。
    assert summary["duplicate_conflicts"] == 1
    assert summary["proposals"] == 1


async def test_a_checkpointed_batch_is_skipped_on_retry(harness):
    harness.session.scalar = AsyncMock(return_value=SimpleNamespace(
        redaction_hits={tasks.CHECKPOINT_KEY: {"fingerprint": "x", "proposal_ids": [7]}}))

    summary = await tasks.run_detection(harness.session, run_key="t1")

    assert summary["skipped_batches"] == 1
    assert summary["resumed_proposal_ids"] == [7]


async def test_the_checkpoint_takes_an_advisory_lock(harness):
    await tasks.run_detection(harness.session, run_key="t1")

    stmt, params = harness.session.execute.await_args.args
    assert "pg_advisory_xact_lock" in str(stmt)
    assert isinstance(params["key"], int)


def test_the_task_is_registered_with_the_long_timeout():
    # 逐批调 provider 的任务远超默认 15 分钟；裸注册等于每 15 分钟被杀一次。
    entry = next(
        f for f in WorkerSettings.functions
        if getattr(f, "coroutine", f).__name__ == "detect_conflicts"
    )
    assert entry.timeout_s == LONG_JOB_TIMEOUT
