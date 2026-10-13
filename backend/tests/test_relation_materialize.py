import pytest
from sqlalchemy import select

from app.controls.models import Control, ControlRelation, RelationType
from app.errors import AppError, Conflict
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.review.models import Proposal, ProposalKind, ProposalStatus
from app.review.service import Decision, decide

FROM_TEXT = "Every change must be approved by the CAB before implementation."
TO_TEXT = "The implementer shall follow the approved implementation plan."
_QUOTES = {
    FROM_TEXT: "approved by the CAB",
    TO_TEXT: "follow the approved implementation plan",
}


async def _setup(db_session):
    actor = User(email="lead@example.com", name="Lead", role=Role.GRC_LEAD,
                 password_hash=hash_password("pw123456"))
    left = Control(code="C-0001", title="Approval", statement=FROM_TEXT)
    right = Control(code="C-0002", title="Implementation", statement=TO_TEXT)
    db_session.add_all([actor, left, right])
    await db_session.flush()
    return actor, left, right


async def _proposal(db_session, left, right, *, relation_type="depends_on"):
    from_quote, to_quote = _QUOTES[left.statement], _QUOTES[right.statement]
    payload = {
        "from_control_id": left.id, "to_control_id": right.id,
        "from_quote": from_quote,
        "to_quote": to_quote,
        "rationale": "implementation depends on prior approval",
        "confidence": 0.8, "relation_type": relation_type,
    }
    proposal = Proposal(
        kind=ProposalKind.RELATION, status=ProposalStatus.PENDING,
        payload=payload,
        citations=[
            {"control_id": left.id, "quote": from_quote},
            {"control_id": right.id, "quote": to_quote},
        ],
        confidence=0.8,
    )
    db_session.add(proposal)
    await db_session.flush()
    return proposal


@pytest.mark.asyncio
async def test_accepting_creates_the_relation_with_its_direction_kept(db_session):
    actor, left, right = await _setup(db_session)
    proposal = await _proposal(db_session, left, right)
    await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)

    row = await db_session.scalar(select(ControlRelation))
    assert (row.from_control_id, row.to_control_id) == (left.id, right.id)
    assert row.relation_type == RelationType.DEPENDS_ON
    assert row.confirmed_by == actor.id and row.confirmed_at is not None


@pytest.mark.asyncio
async def test_duplicates_are_canonicalised_low_to_high(db_session):
    """对称关系若不规范化，A→B 与 B→A 会各存一条互为镜像的记录。"""
    actor, left, right = await _setup(db_session)
    proposal = await _proposal(db_session, right, left, relation_type="duplicates")
    await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)

    row = await db_session.scalar(select(ControlRelation))
    assert row.from_control_id < row.to_control_id


@pytest.mark.asyncio
async def test_the_mirrored_duplicate_is_rejected_as_a_conflict(db_session):
    actor, left, right = await _setup(db_session)
    first = await _proposal(db_session, left, right, relation_type="duplicates")
    await decide(db_session, first.id, actor=actor, decision=Decision.ACCEPT)

    second = await _proposal(db_session, right, left, relation_type="duplicates")
    with pytest.raises(Conflict):
        await decide(db_session, second.id, actor=actor, decision=Decision.ACCEPT)


@pytest.mark.asyncio
async def test_a_rejected_proposal_writes_no_relation(db_session):
    actor, left, right = await _setup(db_session)
    proposal = await _proposal(db_session, left, right)
    await decide(db_session, proposal.id, actor=actor,
                 decision=Decision.REJECT, reason="不是依赖，只是同章节")
    assert await db_session.scalar(select(ControlRelation)) is None


@pytest.mark.asyncio
async def test_modified_content_is_revalidated_against_both_statements(db_session):
    actor, left, right = await _setup(db_session)
    proposal = await _proposal(db_session, left, right)
    bad = {**proposal.payload, "to_quote": "fabricated wording"}
    with pytest.raises(AppError):
        await decide(db_session, proposal.id, actor=actor,
                     decision=Decision.MODIFY, payload=bad)
    assert await db_session.scalar(select(ControlRelation)) is None
    await db_session.refresh(proposal)
    assert proposal.status == ProposalStatus.PENDING


@pytest.mark.asyncio
async def test_an_unknown_relation_type_is_rejected(db_session):
    actor, left, right = await _setup(db_session)
    proposal = await _proposal(db_session, left, right, relation_type="implements")
    with pytest.raises(AppError):
        await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)
