"""查漏发现必须有证据，空清单仍检查，第三阶段失败可以独立恢复。"""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.extraction import tasks
from app.extraction.coverage import extraction_coverage
from app.extraction.models import ExtractionInventory
from app.extraction.semantic_review import REVIEW_SCHEMA, SemanticReviewValidator
from app.llm.budget import BudgetExceeded
from app.llm.validation import ValidationFailure, validate
from app.review.models import Proposal, ProposalStatus
from tests.test_extraction_inventory import install_runner, requirement
from tests.test_extraction_tasks_db import extraction_document as document_fixture

extraction_document = document_fixture


def review(cid=1):
    return {"checked_clause_ids": [cid], "findings": [{
        "kind": "missing_requirement", "clause_ids": [cid], "requirement_ids": [],
        "control_indexes": [], "detail": "The approval duty was not inventoried.",
        "citations": [{"clause_id": cid, "quote": "Two approvers are required."}],
    }]}


@pytest.mark.parametrize("change, message", [
    (lambda p: p.update(checked_clause_ids=[]), "every target"),
    (lambda p: p.update(checked_clause_ids=[1, 1]), "every target"),
    (lambda p: p["findings"][0].update(clause_ids=[9]), "target clauses"),
    (lambda p: p["findings"][0].update(citations=[{"clause_id": 2, "quote": "Context"}]), "supporting quote"),
    (lambda p: p["findings"][0].update(requirement_ids=["R2"]), "unknown requirement"),
    (lambda p: p["findings"][0].update(control_indexes=[1]), "unknown control"),
])
async def test_review_requires_complete_inspection_and_valid_references(change, message):
    data = review()
    change(data)
    validator = SemanticReviewValidator(AsyncMock(check=AsyncMock(return_value=None)),
                                        [1], [requirement()], [{}])
    assert message in await validator.check(data)


def test_review_schema_requires_evidence_and_disallows_claimed_certification():
    data = review()
    validate(data, REVIEW_SCHEMA)
    bad = deepcopy(data)
    bad["findings"][0]["citations"] = []
    with pytest.raises(ValidationFailure):
        validate(bad, REVIEW_SCHEMA)
    bad = deepcopy(data)
    bad["complete"] = True
    with pytest.raises(ValidationFailure):
        validate(bad, REVIEW_SCHEMA)


async def test_review_finds_omission_even_when_inventory_is_empty(db_session, extraction_document, monkeypatch):
    doc, clause = extraction_document
    calls = install_runner(monkeypatch, clause.id, empty=True, review_payload=review(clause.id))
    result = await tasks.run_extraction(db_session, doc.id)
    assert calls == ["inventory", "review"]
    assert result["completed_batches"] == 1 and result["proposals"] == 0
    saved = await db_session.scalar(select(ExtractionInventory))
    assert saved.semantic_review == review(clause.id)
    assert saved.normalization_call_id is None and saved.review_call_id is not None
    row = (await extraction_coverage(db_session, doc.id))["clauses"][0]
    assert row["status"] == "uncited"
    assert row["inventory_status"] == "needs_review"
    assert row["semantic_review_status"] == "findings"
    assert row["semantic_finding_count"] == 1


@pytest.mark.parametrize("failure", [ValidationFailure("Invalid review quote"), BudgetExceeded("Budget exhausted")])
async def test_failed_review_resumes_without_regenerating_controls(db_session, extraction_document, monkeypatch, failure):
    doc, clause = extraction_document
    calls = install_runner(monkeypatch, clause.id, review_failure=failure)
    first = await tasks.run_extraction(db_session, doc.id)
    assert calls == ["inventory", "normalization", "review"]
    assert first["completed_batches"] == 0
    saved = await db_session.scalar(select(ExtractionInventory))
    assert saved.normalized_payload["controls"] and saved.semantic_review is None
    assert await db_session.scalar(select(func.count()).select_from(Proposal).where(
        Proposal.status == ProposalStatus.PENDING,
    )) == 0
    row = (await extraction_coverage(db_session, doc.id))["clauses"][0]
    assert row["status"] == "not_completed" and row["semantic_review_status"] == "not_completed"
    calls = install_runner(monkeypatch, clause.id)
    second = await tasks.run_extraction(db_session, doc.id)
    assert calls == ["review"]
    assert second["completed_batches"] == 1
    assert len(second["llm_call_ids"]) == 1
    assert await db_session.scalar(select(func.count()).select_from(ExtractionInventory)) == 1


async def test_flagged_control_remains_a_proposal_and_findings_do_not_disappear_when_cited(
    db_session, extraction_document, monkeypatch,
):
    doc, clause = extraction_document
    data = review(clause.id)
    data["findings"][0].update(kind="uncertain", requirement_ids=["R1"], control_indexes=[0])
    install_runner(monkeypatch, clause.id, review_payload=data)
    result = await tasks.run_extraction(db_session, doc.id)
    proposal = await db_session.get(Proposal, result["proposal_ids"][0])
    assert proposal.status == ProposalStatus.PENDING
    row = (await extraction_coverage(db_session, doc.id))["clauses"][0]
    assert row["status"] == "cited" and row["inventory_status"] == "needs_review"
    assert row["semantic_finding_count"] == 1
    clause.text += " A new condition."
    await db_session.flush()
    changed = (await extraction_coverage(db_session, doc.id))["clauses"][0]
    assert changed["inventory_id"] is None
    assert changed["semantic_review_status"] == "not_completed"


async def test_fabricated_review_quote_rejects_batch(db_session, extraction_document, monkeypatch):
    doc, clause = extraction_document
    data = review(clause.id)
    data["findings"][0]["citations"][0]["quote"] = "This text is fabricated."
    install_runner(monkeypatch, clause.id, review_payload=data)
    result = await tasks.run_extraction(db_session, doc.id)
    assert result["rejected"] == 1 and result["completed_batches"] == 0
    saved = await db_session.scalar(select(ExtractionInventory))
    assert saved.normalized_payload is not None and saved.semantic_review is None


def test_semantic_contract_is_part_of_cache_identity(monkeypatch):
    before = tasks.fingerprint(1, "body", None)
    monkeypatch.setattr(tasks, "REVIEW_SYSTEM", "new review contract")
    assert before != tasks.fingerprint(1, "body", None)


async def test_flagged_batch_is_visible_and_cannot_be_bulk_accepted(db_session, extraction_document, monkeypatch):
    from app.iam.permissions import Role
    from app.review import service
    from app.review.router import page_context, present
    from app.review.thresholds import Thresholds
    from tests.test_clause_edit import _user

    doc, clause = extraction_document
    doc.ocr_quality_flag = False
    await db_session.flush()
    install_runner(monkeypatch, clause.id, review_payload=review(clause.id))
    result = await tasks.run_extraction(db_session, doc.id)
    proposal = await db_session.get(Proposal, result["proposal_ids"][0])
    limits = Thresholds(auto_accept=0.8, force_manual=0.6)
    # 没有复核发现时这条本可批量；不能让 OCR 或置信度替语义闸门挡住测试。
    assert service.eligible(proposal, limits, ocr_flag=False)
    assert (await service.eligibility(db_session, proposal, limits))[0] is False
    context = await page_context(db_session, [proposal])
    output = present(proposal, limits, context)
    assert output.bulk_acceptable is False
    assert output.extraction_review["finding_count"] == 1
    actor = await _user(db_session, Role.GRC_LEAD)
    assert await service.bulk_accept(db_session, [proposal.id], actor=actor) == {"accepted": 0, "skipped": 1}
    await db_session.refresh(proposal)
    assert proposal.status == ProposalStatus.PENDING
