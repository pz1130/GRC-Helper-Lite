from app.clauses.models import Clause
from app.conflicts.citations import ConflictCitationValidator
from app.ingest.models import DocType, Document


async def _clause(db_session, document_id: int, number: str, text: str) -> Clause:
    clause = Clause(
        document_id=document_id, number=number, heading="H",
        heading_path=f"H › {number}", citation_label=number,
        text=text, order_index=0, level=1,
    )
    db_session.add(clause)
    await db_session.flush()
    return clause


async def _doc(db_session, index: int) -> Document:
    doc = Document(
        title=f"Doc {index}", doc_type=DocType.POLICY, file_hash=str(index) * 64,
        file_path=f"/{index}.pdf", original_filename=f"{index}.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    return doc


def _entry(**overrides):
    return {
        "clause_a_id": 1, "clause_b_id": 2,
        "topic": "密码轮换周期",
        "difference": "一处 90 天，一处 180 天",
        "quote_a": "every 90 days", "quote_b": "every 180 days",
        "confidence": 0.8, **overrides,
    }


async def _validator(db_session):
    doc_a = await _doc(db_session, 1)
    doc_b = await _doc(db_session, 2)
    a = await _clause(db_session, doc_a.id, "4.2", "Passwords rotate every 90 days.")
    b = await _clause(db_session, doc_b.id, "7.1", "Passwords rotate every 180 days.")
    return ConflictCitationValidator(db_session, clause_ids=[a.id, b.id]), a, b


async def test_a_well_formed_conflict_passes(db_session):
    validator, a, b = await _validator(db_session)
    payload = {"conflicts": [_entry(clause_a_id=a.id, clause_b_id=b.id)]}

    assert await validator.check(payload) is None


async def test_a_clause_outside_the_batch_is_rejected(db_session):
    validator, a, _ = await _validator(db_session)
    payload = {"conflicts": [_entry(clause_a_id=a.id, clause_b_id=999_999)]}

    assert "is not in this batch" in (await validator.check(payload) or "")


async def test_a_quote_that_is_not_in_the_clause_is_rejected(db_session):
    validator, a, b = await _validator(db_session)
    payload = {"conflicts": [
        _entry(clause_a_id=a.id, clause_b_id=b.id, quote_a="every 45 days")
    ]}

    assert "was not found" in (await validator.check(payload) or "")


async def test_both_sides_being_the_same_clause_is_rejected(db_session):
    validator, a, _ = await _validator(db_session)
    payload = {"conflicts": [_entry(clause_a_id=a.id, clause_b_id=a.id)]}

    assert "cannot be the same clause" in (await validator.check(payload) or "")


async def test_an_empty_answer_needs_the_insufficient_evidence_flag(db_session):
    validator, _, _ = await _validator(db_session)

    assert await validator.check({"conflicts": [], "insufficient_evidence": True}) is None
    assert await validator.check({"conflicts": []}) is not None
