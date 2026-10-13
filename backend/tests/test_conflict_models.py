import pytest
from sqlalchemy.exc import IntegrityError

from app.clauses.models import Clause
from app.conflicts.models import PolicyConflict, normalise_pair
from app.ingest.models import DocType, Document


async def _clause(db_session, document_id: int, number: str) -> Clause:
    clause = Clause(
        document_id=document_id, number=number, heading="H",
        heading_path=f"H › {number}", citation_label=number,
        text=f"Clause {number} text.", order_index=0, level=1,
    )
    db_session.add(clause)
    await db_session.flush()
    return clause


async def _document(db_session, index: int) -> Document:
    doc = Document(
        title=f"Doc {index}", doc_type=DocType.POLICY, file_hash=str(index) * 64,
        file_path=f"/{index}.pdf", original_filename=f"{index}.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    return doc


def test_normalise_pair_orders_the_two_sides():
    assert normalise_pair(7, 3) == (3, 7)
    assert normalise_pair(3, 7) == (3, 7)


def test_normalise_pair_rejects_a_self_pair():
    with pytest.raises(ValueError):
        normalise_pair(5, 5)


async def test_the_same_pair_and_topic_cannot_be_stored_twice(db_session):
    doc_a = await _document(db_session, 1)
    doc_b = await _document(db_session, 2)
    clause_a = await _clause(db_session, doc_a.id, "4.2")
    clause_b = await _clause(db_session, doc_b.id, "7.1")

    low, high = normalise_pair(clause_a.id, clause_b.id)
    db_session.add(PolicyConflict(
        clause_a_id=low, clause_b_id=high, topic="密码轮换周期",
        difference="一处 90 天，一处 180 天", confidence=0.8,
    ))
    await db_session.flush()

    db_session.add(PolicyConflict(
        clause_a_id=low, clause_b_id=high, topic="密码轮换周期",
        difference="换个说法", confidence=0.9,
    ))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_the_same_pair_may_hold_two_different_topics(db_session):
    doc_a = await _document(db_session, 3)
    doc_b = await _document(db_session, 4)
    clause_a = await _clause(db_session, doc_a.id, "4.2")
    clause_b = await _clause(db_session, doc_b.id, "7.1")
    low, high = normalise_pair(clause_a.id, clause_b.id)

    db_session.add(PolicyConflict(
        clause_a_id=low, clause_b_id=high, topic="密码轮换周期", difference="90 vs 180"))
    db_session.add(PolicyConflict(
        clause_a_id=low, clause_b_id=high, topic="审批权限", difference="部门经理 vs CAB"))
    await db_session.flush()   # 不该报错——一对制度可以在两处打架
