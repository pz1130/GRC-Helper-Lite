import pytest
from sqlalchemy import select

from app.clauses.models import Clause
from app.conflicts.models import PolicyConflict
from app.errors import AppError
from app.ingest.models import DocType, Document
from app.review.materialize import materialize
from app.review.models import Proposal, ProposalKind, ProposalStatus


async def _doc(db_session, index: int) -> Document:
    doc = Document(
        title=f"Doc {index}", doc_type=DocType.POLICY, file_hash=str(index) * 64,
        file_path=f"/{index}.pdf", original_filename=f"{index}.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    return doc


async def _clause(db_session, document_id: int, number: str, text: str) -> Clause:
    clause = Clause(
        document_id=document_id, number=number, heading="H",
        heading_path=f"H › {number}", citation_label=number,
        text=text, order_index=0, level=1,
    )
    db_session.add(clause)
    await db_session.flush()
    return clause


async def _pair(db_session):
    doc_a = await _doc(db_session, 1)
    doc_b = await _doc(db_session, 2)
    a = await _clause(db_session, doc_a.id, "4.2", "Passwords rotate every 90 days.")
    b = await _clause(db_session, doc_b.id, "7.1", "Passwords rotate every 180 days.")
    return a, b


def _payload(a, b, **overrides):
    return {
        "clause_a_id": a.id, "clause_b_id": b.id,
        "topic": "密码轮换周期", "difference": "一处 90 天，一处 180 天",
        "quote_a": "every 90 days", "quote_b": "every 180 days",
        "confidence": 0.8, **overrides,
    }


async def _proposal(db_session, payload) -> Proposal:
    proposal = Proposal(
        kind=ProposalKind.CONFLICT, payload=payload, citations=[],
        status=ProposalStatus.PENDING, confidence=payload.get("confidence"),
    )
    db_session.add(proposal)
    await db_session.flush()
    return proposal


async def test_confirming_a_conflict_writes_one_row(db_session):
    a, b = await _pair(db_session)
    proposal = await _proposal(db_session, _payload(a, b))

    await materialize(db_session, proposal, _payload(a, b), actor_id=None)

    rows = (await db_session.execute(select(PolicyConflict))).scalars().all()
    assert len(rows) == 1
    assert rows[0].topic == "密码轮换周期"


async def test_the_stored_pair_is_normalised(db_session):
    a, b = await _pair(db_session)
    # 故意把大的放前面
    payload = _payload(a, b, clause_a_id=b.id, clause_b_id=a.id,
                       quote_a="every 180 days", quote_b="every 90 days")
    proposal = await _proposal(db_session, payload)

    await materialize(db_session, proposal, payload, actor_id=None)

    row = (await db_session.execute(select(PolicyConflict))).scalars().one()
    assert row.clause_a_id < row.clause_b_id


async def test_a_quote_that_is_not_in_the_clause_is_refused(db_session):
    a, b = await _pair(db_session)
    payload = _payload(a, b, quote_a="every 45 days")
    proposal = await _proposal(db_session, payload)

    with pytest.raises(AppError):
        await materialize(db_session, proposal, payload, actor_id=None)


async def test_confirming_the_same_conflict_twice_is_idempotent(db_session):
    a, b = await _pair(db_session)
    for _ in range(2):
        payload = _payload(a, b)
        proposal = await _proposal(db_session, payload)
        await materialize(db_session, proposal, payload, actor_id=None)

    rows = (await db_session.execute(select(PolicyConflict))).scalars().all()
    assert len(rows) == 1
