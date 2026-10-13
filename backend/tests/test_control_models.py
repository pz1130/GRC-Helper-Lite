import pytest
from sqlalchemy import select, text

from app.clauses.models import Clause
from app.controls.models import (
    Control,
    ControlRelation,
    ControlSource,
    RelationType,
    SourceRelation,
)
from app.ingest.models import DocType, Document


async def _clause(db_session, *, doc_hash: str, number: str) -> Clause:
    doc = Document(
        title=f"D{doc_hash[:4]}",
        doc_type=DocType.PROCEDURE,
        file_hash=doc_hash,
        file_path="/x.pdf",
        original_filename="x.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    clause = Clause(
        document_id=doc.id,
        number=number,
        heading="H",
        heading_path=f"D › {number}",
        citation_label=number,
        text="Dual approval is required.",
        order_index=0,
        level=1,
    )
    db_session.add(clause)
    await db_session.flush()
    return clause


async def _control(db_session, code: str = "C-001") -> Control:
    control = Control(
        code=code,
        title="Dual approval for privileged accounts",
        statement="Privileged account changes need two approvers.",
    )
    db_session.add(control)
    await db_session.flush()
    return control


@pytest.mark.asyncio
async def test_control_persists_with_defaults(db_session):
    control = await _control(db_session)
    assert control.status == "active"
    assert control.owner_user_id is None


@pytest.mark.asyncio
async def test_control_code_is_unique(db_session):
    from sqlalchemy.exc import IntegrityError

    await _control(db_session)
    with pytest.raises(IntegrityError):
        await _control(db_session)


@pytest.mark.asyncio
async def test_one_control_can_be_backed_by_clauses_from_several_documents(db_session):
    """spec §5.3：这正是"不同文件里体现的控制点"的实现基础。"""
    control = await _control(db_session)
    first = await _clause(db_session, doc_hash="a" * 64, number="4")
    second = await _clause(db_session, doc_hash="b" * 64, number="7.2")

    for clause, relation in ((first, SourceRelation.DEFINES), (second, SourceRelation.ELABORATES)):
        db_session.add(ControlSource(control_id=control.id, clause_id=clause.id, relation=relation))
    await db_session.flush()

    rows = list(await db_session.scalars(select(ControlSource)))
    assert len(rows) == 2
    assert {r.clause_id for r in rows} == {first.id, second.id}


@pytest.mark.asyncio
async def test_source_relation_persists_spec_value_not_enum_name(db_session):
    control = await _control(db_session)
    clause = await _clause(db_session, doc_hash="c" * 64, number="1")
    db_session.add(
        ControlSource(
            control_id=control.id, clause_id=clause.id, relation=SourceRelation.ELABORATES
        )
    )
    await db_session.flush()

    raw = await db_session.scalar(text("select relation from control_sources limit 1"))
    assert raw == "elaborates"


@pytest.mark.asyncio
async def test_same_clause_cannot_back_a_control_twice(db_session):
    from sqlalchemy.exc import IntegrityError

    control = await _control(db_session)
    clause = await _clause(db_session, doc_hash="d" * 64, number="1")
    for _ in range(2):
        db_session.add(
            ControlSource(
                control_id=control.id, clause_id=clause.id, relation=SourceRelation.DEFINES
            )
        )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_control_relations_carry_a_type_and_rationale(db_session):
    first = await _control(db_session, code="C-001")
    second = await _control(db_session, code="C-002")
    db_session.add(
        ControlRelation(
            from_control_id=second.id,
            to_control_id=first.id,
            relation_type=RelationType.IMPLEMENTS,
            rationale="The procedure implements the policy requirement.",
            confidence=0.82,
        )
    )
    await db_session.flush()

    relation = await db_session.scalar(select(ControlRelation))
    assert relation.relation_type is RelationType.IMPLEMENTS
    assert relation.confidence == pytest.approx(0.82)


@pytest.mark.asyncio
async def test_conflicts_with_is_an_allowed_relation(db_session):
    """M10 的制度冲突检测要用它，取值先建好。"""
    first = await _control(db_session, code="C-001")
    second = await _control(db_session, code="C-002")
    db_session.add(
        ControlRelation(
            from_control_id=first.id,
            to_control_id=second.id,
            relation_type=RelationType.CONFLICTS_WITH,
            rationale="90 days vs 180 days",
        )
    )
    await db_session.flush()
    raw = await db_session.scalar(text("select relation_type from control_relations limit 1"))
    assert raw == "conflicts_with"


@pytest.mark.asyncio
async def test_deleting_a_control_removes_its_sources(db_session):
    control = await _control(db_session)
    clause = await _clause(db_session, doc_hash="e" * 64, number="1")
    db_session.add(
        ControlSource(control_id=control.id, clause_id=clause.id, relation=SourceRelation.DEFINES)
    )
    await db_session.flush()

    await db_session.delete(control)
    await db_session.flush()
    assert await db_session.scalar(select(ControlSource)) is None


@pytest.mark.asyncio
async def test_deleting_a_clause_removes_the_link_but_not_the_control(db_session):
    """条款没了不代表控制点没了——它可能还有别的文件支撑。"""
    control = await _control(db_session)
    clause = await _clause(db_session, doc_hash="f" * 64, number="1")
    db_session.add(
        ControlSource(control_id=control.id, clause_id=clause.id, relation=SourceRelation.DEFINES)
    )
    await db_session.flush()

    await db_session.delete(clause)
    await db_session.flush()

    assert await db_session.scalar(select(ControlSource)) is None
    assert await db_session.scalar(select(Control)) is not None
