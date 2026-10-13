import pytest
from sqlalchemy import select, text

from app.review.models import Proposal, ProposalKind, ProposalStatus


async def _proposal(db_session, **kwargs) -> Proposal:
    defaults = {
        "kind": ProposalKind.CONTROL_EXTRACT,
        "payload": {"title": "Dual approval", "statement": "Two approvers required."},
        "citations": [{"clause_id": 1, "quote": "two approvers"}],
        "confidence": 0.87,
    }
    defaults.update(kwargs)
    proposal = Proposal(**defaults)
    db_session.add(proposal)
    await db_session.flush()
    return proposal


@pytest.mark.asyncio
async def test_new_proposal_is_pending(db_session):
    proposal = await _proposal(db_session)
    assert proposal.status is ProposalStatus.PENDING
    assert proposal.decided_by is None
    assert proposal.decided_at is None


@pytest.mark.asyncio
async def test_payload_and_citations_round_trip(db_session):
    await _proposal(db_session)
    found = await db_session.scalar(select(Proposal))
    assert found.payload["title"] == "Dual approval"
    assert found.citations[0]["clause_id"] == 1


@pytest.mark.asyncio
async def test_status_persists_spec_value_not_enum_name(db_session):
    await _proposal(db_session, status=ProposalStatus.MODIFIED)
    raw = await db_session.scalar(text("select status from proposals limit 1"))
    assert raw == "modified"


@pytest.mark.asyncio
async def test_kind_persists_spec_value(db_session):
    await _proposal(db_session, kind=ProposalKind.MATRIX_MAPPING)
    raw = await db_session.scalar(text("select kind from proposals limit 1"))
    assert raw == "matrix_mapping"


@pytest.mark.asyncio
async def test_all_eight_ai_task_kinds_plus_matrix_are_representable(db_session):
    """spec §6.5 的八个 AI 任务，加上 M4 的 Excel 列映射。"""
    expected = {
        "control_extract",
        "mapping",
        "relation",
        "conflict",
        "answer",
        "maturity_score",
        "evidence_suggestion",
        "audit_prediction",
        "matrix_mapping",
    }
    assert {kind.value for kind in ProposalKind} == expected


@pytest.mark.asyncio
async def test_decided_payload_keeps_what_the_human_actually_approved(db_session):
    """人工改过之后，落库的应当是改后的版本，原始 AI 输出仍保留在 payload 里。"""
    proposal = await _proposal(db_session)
    proposal.status = ProposalStatus.MODIFIED
    proposal.decided_payload = {"title": "Dual approval (edited)", "statement": "..."}
    await db_session.flush()

    found = await db_session.scalar(select(Proposal))
    assert found.payload["title"] == "Dual approval"
    assert found.decided_payload["title"] == "Dual approval (edited)"


@pytest.mark.asyncio
async def test_document_id_allows_filtering_the_queue_by_document(db_session):
    await _proposal(db_session, document_id=None)
    found = await db_session.scalar(select(Proposal))
    assert found.document_id is None


@pytest.mark.asyncio
async def test_reject_reason_is_recorded(db_session):
    proposal = await _proposal(db_session, status=ProposalStatus.REJECTED)
    proposal.reject_reason = "复述条款原文，不是控制点"
    await db_session.flush()
    found = await db_session.scalar(select(Proposal))
    assert "复述" in found.reject_reason
