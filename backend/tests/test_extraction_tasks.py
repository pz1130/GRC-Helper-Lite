"""纯编排测试：不连数据库，也不用共享 fixture。"""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.errors import Conflict, NotFound
from app.extraction import tasks
from app.extraction.prompts import EXTRACT_SCHEMA
from app.ingest.models import DocStatus, Document
from app.llm.runner import ValidatedResult
from app.llm.validation import ValidationFailure, validate


def item():
    return {"title": "Dual approval", "statement": "Two approvers are required.",
            "confidence": 0.9,
            "citations": [{"clause_id": 1, "quote": "Two approvers"}]}


@pytest.fixture
def harness(monkeypatch):
    call = SimpleNamespace(id=10, redaction_hits={"dictionary": 2})
    doc = SimpleNamespace(id=7, status=DocStatus.ACTIVE)
    clauses = [SimpleNamespace(id=1, document_id=7, level=1, heading="Approval",
                               citation_label="4.1", text="Two approvers are required.")]
    session = MagicMock()
    session.get = AsyncMock(side_effect=lambda model, key: doc if model is Document else call)
    session.scalars = AsyncMock(return_value=clauses)
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    # 假会话答不了路由查询；检查点指纹里的模型名固定下来。
    monkeypatch.setattr(tasks, "routed_model", AsyncMock(return_value="model-a"))

    async def runner(session, **kwargs):
        data = {"controls": [item(), item()]}
        validate(data, kwargs["schema"])
        reason = await kwargs["citation_validator"].check(data)
        if reason:
            raise ValidationFailure(reason)
        return ValidatedResult(data, None, 10)

    run = AsyncMock(side_effect=runner)
    create = AsyncMock(side_effect=[SimpleNamespace(id=21), SimpleNamespace(id=22)])
    monkeypatch.setattr(tasks, "run", run)
    # 评审单的创建归协调方负责，这里验的是双方约定的契约。
    monkeypatch.setattr(tasks.review_service, "create", create, raising=False)
    return SimpleNamespace(session=session, call=call, doc=doc, clauses=clauses,
                           run=run, create=create)


async def test_proposals_via_review_and_atomic_checkpoint(harness):
    h = harness
    result = await tasks.run_extraction(h.session, 7)
    assert result["proposal_ids"] == [21, 22]
    assert result["llm_call_ids"] == [10]
    assert result["proposals"] == 2
    assert result["completed_batches"] == result["attempted_batches"] == 1
    assert h.call.redaction_hits["dictionary"] == 2
    assert h.call.redaction_hits[tasks.CHECKPOINT_KEY]["proposal_ids"] == [21, 22]
    h.session.begin_nested.assert_called_once()
    # 每批两次提交：先释放检查点的 advisory 锁（锁不得跨越 LLM 调用），
    # 再提交批次成果。
    assert h.session.commit.await_count == 2
    h.session.add.assert_not_called()
    kwargs = h.create.await_args.kwargs
    assert kwargs["document_id"] == 7
    assert kwargs["llm_call_id"] == 10
    assert kwargs["citations"] == item()["citations"]
    assert kwargs["confidence"] == 0.9
    validator = h.run.await_args.kwargs["citation_validator"]
    assert validator._document_id == 7 and validator._clause_ids == {1}


async def test_resume_excludes_cached_work_from_evaluation(harness):
    h = harness
    h.call.redaction_hits[tasks.CHECKPOINT_KEY] = {"proposal_ids": [90]}
    h.session.scalar.return_value = h.call
    result = await tasks.run_extraction(h.session, 7)
    assert result["skipped_batches"] == 1
    assert result["completed_batches"] == result["attempted_batches"] == 0
    assert result["proposal_ids"] == result["llm_call_ids"] == []
    assert result["resumed_proposal_ids"] == [90]
    assert result["resumed_llm_call_ids"] == [10]
    h.run.assert_not_awaited()
    h.create.assert_not_awaited()


async def test_empty_success_is_checkpointed(harness):
    h = harness
    h.run.side_effect = None
    h.run.return_value = ValidatedResult(
        {"controls": [], "insufficient_evidence": True}, None, 10,
    )
    result = await tasks.run_extraction(h.session, 7)
    assert result["completed_batches"] == 1 and result["proposals"] == 0
    assert h.call.redaction_hits[tasks.CHECKPOINT_KEY]["proposal_ids"] == []
    h.create.assert_not_awaited()


async def test_rejected_batch_preserves_trace_without_checkpoint(harness):
    h = harness
    h.run.side_effect = ValidationFailure("fabricated quote")
    result = await tasks.run_extraction(h.session, 7)
    assert result["rejected"] == result["attempted_batches"] == 1
    assert result["completed_batches"] == 0
    assert tasks.CHECKPOINT_KEY not in h.call.redaction_hits
    # 每批两次提交：先释放检查点的 advisory 锁（锁不得跨越 LLM 调用），
    # 再提交批次成果。
    assert h.session.commit.await_count == 2
    h.create.assert_not_awaited()


async def test_provider_failure_preserves_trace_and_propagates(harness):
    h = harness
    h.run.side_effect = RuntimeError("provider unavailable")
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await tasks.run_extraction(h.session, 7)
    # 每批两次提交：先释放检查点的 advisory 锁（锁不得跨越 LLM 调用），
    # 再提交批次成果。
    assert h.session.commit.await_count == 2
    h.create.assert_not_awaited()


async def test_a_provider_that_keeps_failing_leaves_a_visible_row_and_still_propagates(harness, monkeypatch):
    """provider 重试用完仍失败：原来任务直接死掉，审核队列里一行都没有（K16）。
    现在先留一行可见的失败记录，再照旧抛出——worker 的任务级重试靠这个异常，
    重跑成功后 supersede_failed_batch 会按指纹把这行撤下。"""
    from app.llm.providers.base import ProviderError

    h = harness
    h.run.side_effect = ProviderError("网络错误: RemoteProtocolError", retryable=True)
    failed = AsyncMock(return_value=SimpleNamespace(id=61))
    monkeypatch.setattr(tasks.review_service, "record_failed_batch", failed)
    monkeypatch.setattr(tasks.review_service, "has_failed_batch", AsyncMock(return_value=False))
    with pytest.raises(ProviderError):
        await tasks.run_extraction(h.session, 7)
    kwargs = failed.await_args.kwargs
    assert kwargs["clause_ids"] == [1]
    assert kwargs["batch_fingerprint"]
    # 原样显示在审核队列里，所以是英文；具体错误留在 llm_call.error 里
    assert kwargs["reason"].isascii() and "retry" in kwargs["reason"].lower()
    h.create.assert_not_awaited()


async def test_repeated_provider_failures_do_not_pile_up_rows(harness, monkeypatch):
    """worker 最多重试三次；同一批次已经有失败行时不再加一行。"""
    from app.llm.providers.base import ProviderError

    h = harness
    h.run.side_effect = ProviderError("网络错误: RemoteProtocolError", retryable=True)
    failed = AsyncMock(return_value=SimpleNamespace(id=61))
    monkeypatch.setattr(tasks.review_service, "record_failed_batch", failed)
    monkeypatch.setattr(tasks.review_service, "has_failed_batch", AsyncMock(return_value=True))
    with pytest.raises(ProviderError):
        await tasks.run_extraction(h.session, 7)
    failed.assert_not_awaited()


async def test_partial_batch_failure_rolls_back_savepoint(harness):
    h = harness
    h.create.side_effect = [SimpleNamespace(id=21), RuntimeError("review failed")]
    h.session.begin_nested.return_value.__aexit__.return_value = False
    with pytest.raises(RuntimeError, match="review failed"):
        await tasks.run_extraction(h.session, 7)
    exit_args = h.session.begin_nested.return_value.__aexit__.await_args.args
    assert exit_args[0] is RuntimeError
    assert tasks.CHECKPOINT_KEY not in h.call.redaction_hits
    # 每批两次提交：先释放检查点的 advisory 锁（锁不得跨越 LLM 调用），
    # 再提交批次成果。
    assert h.session.commit.await_count == 2


async def test_resume_after_later_batch_failure(harness):
    h = harness
    h.clauses.append(SimpleNamespace(id=2, document_id=7, level=1, heading="Retention",
                                   citation_label="5", text="Keep audit records."))
    h.run.side_effect = [ValidatedResult({"controls": [item()]}, None, 10),
                         RuntimeError("interrupted")]
    with pytest.raises(RuntimeError, match="interrupted"):
        await tasks.run_extraction(h.session, 7, run_key="job")
    assert h.call.redaction_hits[tasks.CHECKPOINT_KEY]["proposal_ids"] == [21]
    h.session.scalar.side_effect = [h.call, None]
    h.run.side_effect = [ValidatedResult({"controls": [], "insufficient_evidence": True}, None, 11)]
    result = await tasks.run_extraction(h.session, 7, run_key="job")
    assert result["skipped_batches"] == result["completed_batches"] == 1
    assert result["resumed_proposal_ids"] == [21]
    assert result["proposal_ids"] == []


@pytest.mark.parametrize("status", [DocStatus.UPLOADED, DocStatus.PARSING, DocStatus.SUPERSEDED])
async def test_non_active_document_rejected(harness, status):
    harness.doc.status = status
    with pytest.raises(Conflict):
        await tasks.run_extraction(harness.session, 7)
    harness.run.assert_not_awaited()


async def test_missing_document_and_no_clauses(harness):
    h = harness
    h.session.get.side_effect = None
    h.session.get.return_value = None
    with pytest.raises(NotFound):
        await tasks.run_extraction(h.session, 7)
    h.session.get.return_value = h.doc
    h.session.scalars.return_value = []
    assert (await tasks.run_extraction(h.session, 7))["batches"] == 0


def test_checkpoint_identity_changes_for_run_source_document_or_prompt(monkeypatch):
    key = tasks.fingerprint(7, "body", "job")
    assert key == tasks.fingerprint(7, "body", "job")
    assert key != tasks.fingerprint(8, "body", "job")
    assert key != tasks.fingerprint(7, "changed body", "job")
    assert key != tasks.fingerprint(7, "body", "fresh-evaluation")
    monkeypatch.setattr(tasks, "EXTRACT_SYSTEM", "new prompt version")
    assert key != tasks.fingerprint(7, "body", "job")


async def test_checkpoint_query_locks_before_read_and_matches_json(harness):
    h = harness
    key = tasks.fingerprint(7, "body", "job")
    await tasks._checkpoint(h.session, key)
    assert "pg_advisory_xact_lock" in str(h.session.execute.await_args.args[0])
    assert -(2**63) <= h.session.execute.await_args.args[1]["key"] < 2**63
    stmt = h.session.scalar.await_args.args[0]
    compiled = stmt.compile(dialect=postgresql.dialect())
    assert "@>" in str(compiled)
    assert {tasks.CHECKPOINT_KEY: {"fingerprint": key}} in compiled.params.values()


async def test_the_worker_does_not_make_every_enqueue_a_fresh_run(harness, monkeypatch):
    # 原来入口把 job_id 当 run_key 传进去：用户每点一次「抽取」，同样的批次就重新
    # 付费调一次模型、再出一整套重复提案。检查点身份只该是输入 + 模型。
    h = harness

    @asynccontextmanager
    async def factory():
        yield h.session

    runner = AsyncMock(return_value={"proposals": 0})
    monkeypatch.setattr(tasks, "session_factory", factory)
    monkeypatch.setattr(tasks, "run_extraction", runner)
    await tasks.extract_controls({"job_id": "arq-stable-job"}, 7)
    runner.assert_awaited_once_with(h.session, 7)


@pytest.mark.parametrize("data", [
    {"controls": []}, {"controls": [item()], "insufficient_evidence": True},
    {"controls": [{**item(), "citations": []}]},
    {"controls": [{**item(), "citations": [{"clause_id": 1, "quote": "  "}]}]},
])
def test_schema_rejects_unsupported_outputs(data):
    with pytest.raises(ValidationFailure):
        validate(data, EXTRACT_SCHEMA)


def test_schema_allows_abstention():
    validate({"controls": [], "insufficient_evidence": True}, EXTRACT_SCHEMA)


def test_extraction_has_no_business_model_writes():
    import ast

    for path in Path(tasks.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text())
        constructors = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not constructors.intersection({"Control", "ControlSource", "ControlRelation", "Proposal"})


async def test_oversized_batch_is_visible_without_model_call_or_truncation(harness, monkeypatch):
    h = harness
    h.clauses[0].text = "Required evidence. " * 1000
    original = h.clauses[0].text
    failed = AsyncMock(return_value=SimpleNamespace(id=51))
    monkeypatch.setattr(tasks.review_service, "record_failed_batch", failed)
    result = await tasks.run_extraction(h.session, 7)
    assert result["rejected"] == 1
    assert result["failed_proposal_ids"] == [51]
    assert result["attempted_batches"] == 0
    h.run.assert_not_awaited()
    tasks.routed_model.assert_not_awaited()
    assert failed.await_args.kwargs["clause_ids"] == [1]
    assert h.clauses[0].text == original
