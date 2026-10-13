import pytest
from sqlalchemy import select

from app.clauses.models import Clause
from app.controls.models import Control, ControlSource
from app.iam.models import AuditLog, User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.ingest.models import DocStatus, DocType, Document
from app.review.models import Proposal, ProposalKind, ProposalStatus
from app.review.service import Decision, bulk_accept, decide, pending


async def _setup(db_session, *, ocr_flag: bool = False):
    actor = User(
        email="lead@example.com", name="L", role=Role.GRC_LEAD, password_hash=hash_password("pw")
    )
    doc = Document(
        title="P",
        doc_type=DocType.PROCEDURE,
        file_hash="a" * 64,
        file_path="/x.pdf",
        original_filename="x.pdf",
        status=DocStatus.ACTIVE,
        ocr_quality_flag=ocr_flag,
    )
    db_session.add_all([actor, doc])
    await db_session.flush()
    clause = Clause(
        document_id=doc.id,
        number="4.1",
        heading="H",
        heading_path="D › H",
        citation_label="4.1",
        text="Two approvers are required.",
        order_index=0,
        level=1,
    )
    db_session.add(clause)
    await db_session.flush()
    return actor, doc, clause


async def _proposal(
    db_session, doc, clause, *, confidence=0.9, title="Dual approval",
    statement="Two approvers.",
):
    proposal = Proposal(
        kind=ProposalKind.CONTROL_EXTRACT,
        payload={
            "title": title,
            "statement": statement,
            "confidence": confidence,
            "citations": [{"clause_id": clause.id, "quote": "Two approvers"}],
        },
        citations=[{"clause_id": clause.id, "quote": "Two approvers"}],
        confidence=confidence,
        document_id=doc.id,
    )
    db_session.add(proposal)
    await db_session.flush()
    return proposal


@pytest.mark.asyncio
async def test_accept_creates_the_control_and_its_source(db_session):
    actor, doc, clause = await _setup(db_session)
    proposal = await _proposal(db_session, doc, clause)

    await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)

    control = await db_session.scalar(select(Control))
    assert control is not None and control.title == "Dual approval"
    source = await db_session.scalar(select(ControlSource))
    assert source.clause_id == clause.id
    assert source.confirmed_by == actor.id
    assert source.confirmed_at is not None


@pytest.mark.asyncio
async def test_accept_marks_the_proposal_decided(db_session):
    actor, doc, clause = await _setup(db_session)
    proposal = await _proposal(db_session, doc, clause)

    await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)
    assert proposal.status is ProposalStatus.ACCEPTED
    assert proposal.decided_by == actor.id


@pytest.mark.asyncio
async def test_modify_persists_the_edited_version_not_the_original(db_session):
    """人改过之后落库的必须是改后的，原始 AI 输出仍保留在 payload 里。"""
    actor, doc, clause = await _setup(db_session)
    proposal = await _proposal(db_session, doc, clause)

    edited = {**proposal.payload, "title": "Dual approval (reworded)"}
    await decide(db_session, proposal.id, actor=actor, decision=Decision.MODIFY, payload=edited)

    control = await db_session.scalar(select(Control))
    assert control.title == "Dual approval (reworded)"
    assert proposal.payload["title"] == "Dual approval", "原始输出必须留着"
    assert proposal.status is ProposalStatus.MODIFIED


@pytest.mark.asyncio
async def test_reject_creates_nothing_and_records_the_reason(db_session):
    actor, doc, clause = await _setup(db_session)
    proposal = await _proposal(db_session, doc, clause)

    await decide(
        db_session,
        proposal.id,
        actor=actor,
        decision=Decision.REJECT,
        reason="复述条款原文，不是控制点",
    )

    assert await db_session.scalar(select(Control)) is None
    assert proposal.status is ProposalStatus.REJECTED
    assert "复述" in proposal.reject_reason


@pytest.mark.asyncio
async def test_deciding_twice_is_rejected(db_session):
    from app.errors import Conflict

    actor, doc, clause = await _setup(db_session)
    proposal = await _proposal(db_session, doc, clause)
    await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)

    with pytest.raises(Conflict):
        await decide(db_session, proposal.id, actor=actor, decision=Decision.REJECT)


@pytest.mark.asyncio
async def test_decision_is_audited(db_session):
    actor, doc, clause = await _setup(db_session)
    proposal = await _proposal(db_session, doc, clause)
    await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)

    entry = await db_session.scalar(select(AuditLog).where(AuditLog.entity_type == "Proposal"))
    assert entry is not None and entry.user_id == actor.id


@pytest.mark.asyncio
async def test_pending_puts_the_least_confident_first(db_session):
    """低置信度最需要人看，排最前面（spec §7.4）。"""
    _actor, doc, clause = await _setup(db_session)
    await _proposal(db_session, doc, clause, confidence=0.95, title="A")
    await _proposal(db_session, doc, clause, confidence=0.30, title="B")
    await _proposal(db_session, doc, clause, confidence=0.70, title="C")

    rows = await pending(db_session)
    assert [r.payload["title"] for r in rows] == ["B", "C", "A"]


@pytest.mark.asyncio
async def test_bulk_accept_skips_the_ones_below_the_threshold(db_session):
    actor, doc, clause = await _setup(db_session)
    high = await _proposal(db_session, doc, clause, confidence=0.95, title="High")
    low = await _proposal(db_session, doc, clause, confidence=0.50, title="Low")

    result = await bulk_accept(db_session, [high.id, low.id], actor=actor)

    assert result["accepted"] == 1
    assert result["skipped"] == 1
    assert high.status is ProposalStatus.ACCEPTED
    assert low.status is ProposalStatus.PENDING


@pytest.mark.asyncio
async def test_bulk_accept_refuses_everything_from_an_ocr_suspect_document(db_session):
    """spec §9：OCR 存疑文档的提案一律逐条确认，无视置信度。"""
    actor, doc, clause = await _setup(db_session, ocr_flag=True)
    proposal = await _proposal(db_session, doc, clause, confidence=0.99)

    result = await bulk_accept(db_session, [proposal.id], actor=actor)
    assert result["accepted"] == 0
    assert proposal.status is ProposalStatus.PENDING


@pytest.mark.asyncio
async def test_accepting_the_same_control_twice_reuses_it(db_session):
    """同一控制点由两份文件支撑时，第二次确认应当挂到同一个 Control 上，
    而不是造出重复条目——这正是 ControlSource 多对多存在的意义。"""
    actor, doc, clause = await _setup(db_session)
    first = await _proposal(db_session, doc, clause, title="Dual approval")
    await decide(db_session, first.id, actor=actor, decision=Decision.ACCEPT)

    second = await _proposal(db_session, doc, clause, title="Dual approval")
    await decide(db_session, second.id, actor=actor, decision=Decision.ACCEPT)

    controls = list(await db_session.scalars(select(Control)))
    assert len(controls) == 1


def test_only_materialize_writes_the_control_table():
    """铁律 2：Control 与 Mapping 只能由 review.materialize 写入。"""
    from pathlib import Path

    root = Path("backend/app") if Path("backend/app").is_dir() else Path("app")
    allowed = {
        root / "review" / "materialize.py",
        root / "controls" / "models.py",
        root / "frameworks" / "models.py",
    }
    offenders = []
    files = list(root.rglob("*.py"))
    assert len(files) > 50, "源码扫描没有扫到文件，测试会空转"
    for path in files:
        if path in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        if any(name + "(" in source for name in ("Control", "ControlSource", "ControlRelation", "Mapping")):
            offenders.append(str(path))
    assert not offenders, f"这些模块绕开了确认队列直接写正式表：{offenders}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"title": "   "},
        {"statement": None},
        {"title": 123},
        {"citations": []},
        {"citations": [{"clause_id": True, "quote": "Two approvers"}]},
        {"citations": [{"clause_id": 999999, "quote": "Two approvers"}]},
        {"unknown": "field"},
        {"origin": "matrix"},
    ],
)
async def test_invalid_modified_content_has_no_partial_writes(db_session, changes):
    from app.errors import AppError

    actor, doc, clause = await _setup(db_session)
    proposal = await _proposal(db_session, doc, clause)
    proposal_id = proposal.id
    with pytest.raises(AppError):
        await decide(
            db_session,
            proposal_id,
            actor=actor,
            decision=Decision.MODIFY,
            payload={**proposal.payload, **changes},
        )
    await db_session.refresh(proposal)
    assert proposal.status == ProposalStatus.PENDING
    assert await db_session.scalar(select(Control)) is None
    assert await db_session.scalar(select(AuditLog)) is None


@pytest.mark.asyncio
async def test_modified_fabricated_quote_rejected(db_session):
    from app.errors import AppError

    actor, doc, clause = await _setup(db_session)
    proposal = await _proposal(db_session, doc, clause)
    changed = {**proposal.payload, "citations": [{"clause_id": clause.id, "quote": "Invented"}]}
    with pytest.raises(AppError):
        await decide(
            db_session, proposal.id, actor=actor, decision=Decision.MODIFY, payload=changed
        )
    assert await db_session.scalar(select(Control)) is None


@pytest.mark.asyncio
async def test_service_checks_permissions_even_without_router(db_session):
    from app.errors import Forbidden

    actor, doc, clause = await _setup(db_session)
    proposal = await _proposal(db_session, doc, clause)
    actor.role = Role.CONTRIBUTOR
    with pytest.raises(Forbidden):
        await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)
    with pytest.raises(Forbidden):
        await bulk_accept(db_session, [proposal.id], actor=actor)
    assert proposal.status == ProposalStatus.PENDING


@pytest.mark.asyncio
async def test_bulk_rolls_back_prior_accepts_on_invalid_content(db_session):
    from app.errors import AppError

    actor, doc, clause = await _setup(db_session)
    good = await _proposal(db_session, doc, clause, title="Good")
    # 正文必须与 good 不同：正文逐字相同的提案现在会被判为待人工选择而跳过，
    # 那样就走不到「内容非法」这条路，测的东西就变了（OQ-8）。
    bad = await _proposal(db_session, doc, clause, title="Bad", statement="Three approvers.")
    bad.payload = {**bad.payload, "citations": []}
    await db_session.flush()
    good_id, bad_id = good.id, bad.id
    with pytest.raises(AppError):
        await bulk_accept(db_session, [good_id, bad_id], actor=actor)
    await db_session.refresh(good)
    await db_session.refresh(bad)
    assert good.status == bad.status == ProposalStatus.PENDING
    assert await db_session.scalar(select(Control)) is None
    assert await db_session.scalar(select(AuditLog)) is None


@pytest.mark.asyncio
async def test_ocr_gate_also_checks_cited_document_without_document_id(db_session):
    actor, doc, clause = await _setup(db_session, ocr_flag=True)
    proposal = await _proposal(db_session, doc, clause, confidence=1)
    proposal.document_id = None
    await db_session.flush()
    assert await bulk_accept(db_session, [proposal.id], actor=actor) == {
        "accepted": 0,
        "skipped": 1,
    }


@pytest.mark.asyncio
async def test_generated_code_survives_gaps_and_existing_import_codes(db_session):
    actor, doc, clause = await _setup(db_session)
    db_session.add(Control(code="C-0042", title="Existing", statement="Existing"))
    await db_session.flush()
    proposal = await _proposal(db_session, doc, clause)
    await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)
    assert await db_session.scalar(select(Control.id).where(Control.code == "C-0043"))


@pytest.mark.asyncio
async def test_duplicate_citations_materialize_one_source(db_session):
    actor, doc, clause = await _setup(db_session)
    proposal = await _proposal(db_session, doc, clause)
    proposal.payload = {**proposal.payload, "citations": proposal.citations * 2}
    await db_session.flush()
    await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)
    assert len(list(await db_session.scalars(select(ControlSource)))) == 1


@pytest.mark.asyncio
async def test_unsupported_kind_is_not_silently_accepted(db_session):
    from app.errors import AppError

    actor, doc, clause = await _setup(db_session)
    proposal = await _proposal(db_session, doc, clause)
    proposal.kind = ProposalKind.RELATION
    await db_session.flush()
    with pytest.raises(AppError):
        await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)


@pytest.mark.asyncio
async def test_decision_query_locks_and_refreshes_cached_row():
    from unittest.mock import AsyncMock

    from sqlalchemy.dialects import postgresql

    from app.review.service import locked_proposal

    session = AsyncMock()
    await locked_proposal(session, 12)
    statement = session.scalar.call_args.args[0]
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    assert statement.get_execution_options()["populate_existing"] is True


@pytest.mark.asyncio
async def test_create_audits_and_never_materializes(db_session):
    from app.review.service import create

    actor, doc, clause = await _setup(db_session)
    citations = [{"clause_id": clause.id, "quote": "Two approvers"}]
    proposal = await create(
        db_session,
        kind=ProposalKind.CONTROL_EXTRACT,
        payload={"title": "New", "statement": "Statement", "citations": citations},
        citations=citations,
        confidence=0.95,
        document_id=doc.id,
        actor=actor,
    )
    assert proposal.status == ProposalStatus.PENDING
    assert await db_session.scalar(select(Control)) is None
    assert await db_session.scalar(select(AuditLog.id).where(AuditLog.action == "proposal.create"))


async def _matrix_child(db_session, actor):
    mapping = Proposal(
        kind=ProposalKind.MATRIX_MAPPING,
        status=ProposalStatus.ACCEPTED,
        payload={
            "source_sha256": "f" * 64,
            "source_headers": ["Title", "Statement"],
            "source_rows": 1,
            "mapping": {"title": "Title", "statement": "Statement"},
        },
        citations=[],
    )
    db_session.add(mapping)
    await db_session.flush()
    child = Proposal(
        kind=ProposalKind.CONTROL_EXTRACT,
        payload={
            "code": "EXCEL-001",
            "title": "Excel control",
            "statement": "From Excel",
            "citations": [],
            "origin": "matrix",
            "matrix_mapping_proposal_id": mapping.id,
            "source_sha256": "f" * 64,
            "row_number": 1,
            "owner": "Team",
        },
        citations=[],
        confidence=None,
    )
    db_session.add(child)
    await db_session.flush()
    audit = AuditLog(
        user_id=actor.id,
        action="matrix.import",
        entity_type="Proposal",
        entity_id=str(mapping.id),
        after={"proposal_ids": [child.id], "imported": 1, "mapping_proposal_id": mapping.id},
    )
    db_session.add(audit)
    await db_session.flush()
    return mapping, child, audit


@pytest.mark.asyncio
async def test_trusted_matrix_child_needs_no_clause_and_preserves_owner_metadata(db_session):
    actor, _doc, _clause = await _setup(db_session)
    _mapping, child, _audit = await _matrix_child(db_session, actor)
    await decide(db_session, child.id, actor=actor, decision=Decision.ACCEPT)
    control = await db_session.scalar(select(Control))
    assert control.code == "EXCEL-001"
    assert child.decided_payload["owner"] == "Team"
    assert await db_session.scalar(select(ControlSource)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"origin": "not-matrix"},
        {"source_sha256": "a" * 64},
        {"matrix_mapping_proposal_id": 999999},
        {"row_number": 2},
    ],
)
async def test_matrix_child_provenance_cannot_be_modified(db_session, change):
    from app.errors import AppError

    actor, _doc, _clause = await _setup(db_session)
    _mapping, child, _audit = await _matrix_child(db_session, actor)
    with pytest.raises(AppError):
        await decide(
            db_session,
            child.id,
            actor=actor,
            decision=Decision.MODIFY,
            payload={**child.payload, **change},
        )
    assert await db_session.scalar(select(Control)) is None


@pytest.mark.asyncio
async def test_fake_matrix_child_not_in_import_audit_is_rejected(db_session):
    from app.errors import AppError

    actor, _doc, _clause = await _setup(db_session)
    _mapping, child, audit = await _matrix_child(db_session, actor)
    audit.after = {**audit.after, "proposal_ids": []}
    await db_session.flush()
    with pytest.raises(AppError):
        await decide(db_session, child.id, actor=actor, decision=Decision.ACCEPT)


@pytest.mark.asyncio
async def test_mapping_modify_preserves_original_binding(db_session):
    actor, _doc, _clause = await _setup(db_session)
    mapping, _child, _audit = await _matrix_child(db_session, actor)
    mapping.status = ProposalStatus.PENDING
    await db_session.flush()
    await decide(
        db_session,
        mapping.id,
        actor=actor,
        decision=Decision.MODIFY,
        payload={"mapping": {"title": "Title", "statement": "Statement"}},
    )
    assert mapping.decided_payload["source_sha256"] == mapping.payload["source_sha256"]
    assert mapping.decided_payload["source_rows"] == 1
    assert await db_session.scalar(select(Control)) is None


@pytest.mark.asyncio
async def test_invalid_mapping_is_not_accepted(db_session):
    from app.errors import AppError

    actor, _doc, _clause = await _setup(db_session)
    mapping, _child, _audit = await _matrix_child(db_session, actor)
    mapping.status = ProposalStatus.PENDING
    await db_session.flush()
    with pytest.raises(AppError):
        await decide(
            db_session,
            mapping.id,
            actor=actor,
            decision=Decision.MODIFY,
            payload={"mapping": {"title": "Missing column"}},
        )
