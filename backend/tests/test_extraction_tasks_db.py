"""给协调方准备好的数据库集成测试；抽取这一侧不跑它。"""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.clauses.models import Clause
from app.extraction import tasks
from app.ingest.models import DocStatus, DocType, Document
from app.llm.models import LLMCall, RulesetName
from app.llm.runner import ValidatedResult
from app.llm.validation import ValidationFailure
from app.review.models import Proposal, ProposalStatus


@pytest.fixture
async def extraction_document(db_session):
    document = Document(title="Approval", doc_type=DocType.PROCEDURE,
                        file_hash="9" * 64, file_path="/approval.pdf",
                        original_filename="approval.pdf", status=DocStatus.ACTIVE,
                        ocr_quality_flag=True)
    db_session.add(document)
    await db_session.flush()
    clause = Clause(document_id=document.id, number="1", heading="Approval",
                    heading_path="Approval", citation_label="1", level=1, order_index=0,
                    text="Two approvers are required.")
    db_session.add(clause)
    await db_session.flush()
    return document, clause


def fake_runner(monkeypatch, *, empty=False, invalid=False):
    async def run(session, **kwargs):
        clause = await session.scalar(select(Clause).order_by(Clause.id))
        data = {"controls": []} if empty else {"controls": [{
            "title": "Dual approval", "statement": "Two approvers are required.",
            "confidence": 0.95, "citations": [{
                "clause_id": clause.id,
                "quote": "fabricated" if invalid else "Two approvers",
            }],
        }]}
        if empty:
            data["insufficient_evidence"] = True
        reason = await kwargs["citation_validator"].check(data)
        call = LLMCall(task_key="control_extract", model="test", prompt_hash="a" * 64,
                       ruleset=RulesetName.GENERATION, status="error" if reason else "ok",
                       error=reason, redaction_hits={"dictionary": 1})
        session.add(call)
        await session.flush()
        if reason:
            raise ValidationFailure(reason)
        return ValidatedResult(data, None, call.id)

    runner = AsyncMock(side_effect=run)
    monkeypatch.setattr(tasks, "run", runner)
    return runner


async def test_db_extraction_resume_and_fresh_evaluation(db_session, extraction_document, monkeypatch):
    doc, _ = extraction_document
    runner = fake_runner(monkeypatch)
    first = await tasks.run_extraction(db_session, doc.id, run_key="job")
    assert first["proposals"] == 1
    proposal = await db_session.get(Proposal, first["proposal_ids"][0])
    assert proposal.document_id == doc.id
    assert proposal.llm_call_id == first["llm_call_ids"][0]
    second = await tasks.run_extraction(db_session, doc.id, run_key="job")
    assert second["skipped_batches"] == 1
    assert second["proposal_ids"] == []
    assert second["resumed_proposal_ids"] == first["proposal_ids"]
    third = await tasks.run_extraction(db_session, doc.id, run_key="fresh-evaluation")
    assert third["completed_batches"] == 1
    assert third["proposal_ids"] != first["proposal_ids"]
    assert runner.await_count == 2


async def test_db_empty_batch_resumes(db_session, extraction_document, monkeypatch):
    doc, _ = extraction_document
    runner = fake_runner(monkeypatch, empty=True)
    first = await tasks.run_extraction(db_session, doc.id)
    second = await tasks.run_extraction(db_session, doc.id)
    assert first["completed_batches"] == second["skipped_batches"] == 1
    assert first["proposal_ids"] == second["proposal_ids"] == []
    runner.assert_awaited_once()


async def test_coverage_tracks_current_text_and_empty_results(
    db_session, extraction_document, monkeypatch,
):
    from app.extraction.coverage import extraction_coverage

    doc, clause = extraction_document
    before = await extraction_coverage(db_session, doc.id)
    assert before["counts"] == {"cited": 0, "uncited": 0, "not_completed": 1}
    fake_runner(monkeypatch)
    await tasks.run_extraction(db_session, doc.id)
    cited = await extraction_coverage(db_session, doc.id)
    assert cited["counts"] == {"cited": 1, "uncited": 0, "not_completed": 0}

    clause.text += " Retain approval records."
    await db_session.flush()
    changed = await extraction_coverage(db_session, doc.id)
    assert changed["counts"]["not_completed"] == 1

    fake_runner(monkeypatch, empty=True)
    await tasks.run_extraction(db_session, doc.id)
    empty = await extraction_coverage(db_session, doc.id)
    assert empty["counts"] == {"cited": 0, "uncited": 1, "not_completed": 0}
    await tasks.run_extraction(db_session, doc.id)
    assert await extraction_coverage(db_session, doc.id) == empty


async def test_coverage_does_not_treat_failure_or_evaluation_as_completed(
    db_session, extraction_document, monkeypatch,
):
    from app.extraction.coverage import extraction_coverage

    doc, _ = extraction_document
    fake_runner(monkeypatch, invalid=True)
    await tasks.run_extraction(db_session, doc.id)
    assert (await extraction_coverage(db_session, doc.id))["counts"]["not_completed"] == 1
    fake_runner(monkeypatch)
    await tasks.run_extraction(db_session, doc.id, run_key="evaluation")
    assert (await extraction_coverage(db_session, doc.id))["counts"]["not_completed"] == 1


async def test_split_batch_citations_can_use_parent_and_coverage_invalidates_on_parent_edit(
    db_session, extraction_document, monkeypatch,
):
    from app.extraction import coverage
    from app.extraction.batching import build_batches

    doc, parent = extraction_document
    child = Clause(document_id=doc.id, parent_id=parent.id, heading="Review",
                   heading_path="Approval › Review", citation_label="1.1", level=2,
                   order_index=1, text="Review access quarterly.")
    db_session.add(child)
    await db_session.flush()
    def small_batches(clauses):
        return build_batches(clauses, max_chars=100)
    monkeypatch.setattr(tasks, "build_batches", small_batches)
    monkeypatch.setattr(coverage, "build_batches", small_batches)

    async def run(session, **kwargs):
        target_ids = kwargs["citation_validator"]._target_clause_ids
        target = child if child.id in target_ids else parent
        citations = [{"clause_id": target.id, "quote": target.text}]
        if target is child:
            assert parent.text in kwargs["prompt"]
            citations.append({"clause_id": parent.id, "quote": parent.text})
        data = {"controls": [{"title": "Access review", "statement": target.text,
                              "confidence": 0.8, "citations": citations}]}
        assert await kwargs["citation_validator"].check(data) is None
        call = LLMCall(task_key="control_extract", model="test", prompt_hash="a" * 64,
                       ruleset=RulesetName.GENERATION, status="ok", redaction_hits={})
        session.add(call)
        await session.flush()
        return ValidatedResult(data, None, call.id)

    monkeypatch.setattr(tasks, "run", run)
    result = await tasks.run_extraction(db_session, doc.id)
    assert result["completed_batches"] == 2
    assert (await coverage.extraction_coverage(db_session, doc.id))["counts"]["cited"] == 2
    parent.text += " Production only."
    await db_session.flush()
    assert (await coverage.extraction_coverage(db_session, doc.id))["counts"]["not_completed"] == 2


async def test_db_rejection_keeps_error_trace(db_session, extraction_document, monkeypatch):
    doc, _ = extraction_document
    fake_runner(monkeypatch, invalid=True)
    result = await tasks.run_extraction(db_session, doc.id)
    assert result["rejected"] == 1
    call = await db_session.scalar(select(LLMCall))
    assert call.status == "error" and call.error
    assert tasks.CHECKPOINT_KEY not in call.redaction_hits


# ── 失败批次要在队列里留痕 ── OQ-22 ────────────────────────────────
#
# 在此之前，schema 不合格的批次只把 rejected 计数 +1 然后 continue：错误落进
# llm_call.error，worker 日志一条不打，界面只说"任务已入队"。生产栈实测一份
# 37 条款的文档切 7 批，**2 批静默丢掉**——审计员会以为这份制度就只抽出这些
# 控制点。对一个以"不漏控制点"为立身之本的工具，这是正确性问题不是体验问题。


async def test_db_rejected_batch_leaves_a_failed_placeholder(
    db_session, extraction_document, monkeypatch
):
    doc, _ = extraction_document
    fake_runner(monkeypatch, invalid=True)
    await tasks.run_extraction(db_session, doc.id)

    placeholder = await db_session.scalar(select(Proposal))
    assert placeholder is not None, "失败批次必须留下一行，否则这一批就静默消失了"
    assert placeholder.status == ProposalStatus.FAILED
    assert placeholder.document_id == doc.id
    # 失败原因要能直接看懂，不用去翻 llm_call
    assert placeholder.reject_reason
    # 指回那次调用，追得到完整轨迹
    call = await db_session.scalar(select(LLMCall))
    assert placeholder.llm_call_id == call.id


async def test_db_failed_placeholder_is_not_counted_as_pending(
    db_session, extraction_document, monkeypatch
):
    """失败行不是"待人决策"，不该进待确认计数——否则角标永远清不掉。"""
    doc, _ = extraction_document
    fake_runner(monkeypatch, invalid=True)
    await tasks.run_extraction(db_session, doc.id)

    pending = await db_session.scalar(
        select(func.count()).select_from(Proposal).where(Proposal.status == ProposalStatus.PENDING)
    )
    assert pending == 0


async def test_db_successful_retry_supersedes_the_failed_placeholder(
    db_session, extraction_document, monkeypatch
):
    """重跑成功后，那行失败记录要让位——但**不删行**，改成 rejected 并写明原因。

    留着它是审计要求（AuditLog 按 id 记实体）；让它退出队列是为了不制造噪声：
    一条已经被重跑修好的失败，再占着确认队列就是在浪费人的注意力。
    """
    doc, _ = extraction_document
    fake_runner(monkeypatch, invalid=True)
    await tasks.run_extraction(db_session, doc.id)
    failed = await db_session.scalar(select(Proposal))

    # 换成会成功的 runner，重跑同一份文档（失败批次没有 checkpoint，会被重试）
    fake_runner(monkeypatch)
    result = await tasks.run_extraction(db_session, doc.id)
    assert result["proposals"] == 1

    await db_session.refresh(failed)
    assert failed.status == ProposalStatus.REJECTED
    assert "重跑" in (failed.reject_reason or "")


async def test_db_failed_proposal_flush_leaves_no_partial_batch(
    db_session, extraction_document, monkeypatch,
):
    doc, _ = extraction_document
    fake_runner(monkeypatch)
    real_create = tasks.review_service.create

    async def fail_after_create(*args, **kwargs):
        await real_create(*args, **kwargs)
        raise RuntimeError("review failure after write")

    monkeypatch.setattr(tasks.review_service, "create", fail_after_create)
    with pytest.raises(RuntimeError, match="review failure"):
        await tasks.run_extraction(db_session, doc.id)
    assert await db_session.scalar(select(func.count()).select_from(Proposal)) == 0
    call = await db_session.scalar(select(LLMCall))
    assert call is not None
    assert tasks.CHECKPOINT_KEY not in (call.redaction_hits or {})


async def test_clicking_extract_again_reuses_the_batches_it_already_paid_for(
    db_session, extraction_document, monkeypatch
):
    # 入口原来把 job_id 当 run_key：每点一次，同样的批次就重新付费调一次模型、
    # 确认队列里再多一整套重复提案。现在检查点身份 = 输入 + 模型。
    doc, _ = extraction_document
    runner = fake_runner(monkeypatch)
    model = AsyncMock(return_value="model-a")
    monkeypatch.setattr(tasks, "routed_model", model)

    first = await tasks.run_extraction(db_session, doc.id)
    again = await tasks.run_extraction(db_session, doc.id)
    assert again["proposal_ids"] == [] and again["skipped_batches"] == 1
    assert again["resumed_proposal_ids"] == first["proposal_ids"]
    assert runner.await_count == 1

    # 换了模型是一次新的判断：照常重跑
    model.return_value = "model-b"
    switched = await tasks.run_extraction(db_session, doc.id)
    assert switched["completed_batches"] == 1
    assert runner.await_count == 2


async def test_reference_issues_are_reported_before_extraction(db_session, extraction_document):
    from app.extraction.coverage import extraction_coverage

    doc, clause = extraction_document
    clause.text = "Follow section 99 of this document."
    await db_session.flush()
    report = await extraction_coverage(db_session, doc.id)
    assert report["reference_issues"] == [{
        "source_clause_id": clause.id, "label": "99", "status": "missing",
        "target_clause_id": None,
    }]
    assert report["counts"]["not_completed"] == 1
