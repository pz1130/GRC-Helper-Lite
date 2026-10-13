import pytest
from sqlalchemy import select

from app.controls.models import Control
from app.errors import AppError
from app.frameworks.models import Framework, FrameworkItem, Mapping, MappingStrength
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.review.models import Proposal, ProposalKind, ProposalStatus
from app.review.service import Decision, decide

ITEM_TEXT = "Identities and credentials are managed for authorized devices."


async def _setup(db_session):
    actor = User(email="lead@example.com", name="Lead", role=Role.GRC_LEAD,
                 password_hash=hash_password("pw123456"))
    fw = Framework(key="k", name_zh="z", name_en="e", version="1", source="s", item_count=0)
    control = Control(code="C-0001", title="t", statement="s")
    db_session.add_all([actor, fw, control])
    await db_session.flush()
    item = FrameworkItem(framework_id=fw.id, code="PR.AA-01", title="Ident",
                         description=ITEM_TEXT, level=1, order_index=0)
    db_session.add(item)
    await db_session.flush()
    return actor, item, control


async def _proposal(db_session, item, control, *, quote="Identities and credentials are managed",
                    strength="partial"):
    proposal = Proposal(
        kind=ProposalKind.MAPPING, status=ProposalStatus.PENDING,
        payload={
            "framework_item_id": item.id, "control_id": control.id, "strength": strength,
            "framework_item_quote": quote, "rationale": "covers identities", "confidence": 0.9,
        },
        citations=[{"framework_item_id": item.id, "framework_item_quote": quote}], confidence=0.9,
    )
    db_session.add(proposal)
    await db_session.flush()
    return proposal


@pytest.mark.asyncio
async def test_accepting_a_mapping_proposal_creates_the_mapping(db_session):
    actor, item, control = await _setup(db_session)
    proposal = await _proposal(db_session, item, control)
    await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)

    mapping = await db_session.scalar(select(Mapping))
    assert mapping.control_id == control.id
    assert mapping.framework_item_id == item.id
    assert mapping.strength == MappingStrength.PARTIAL
    assert mapping.quote == "Identities and credentials are managed"
    assert mapping.confirmed_by == actor.id
    assert mapping.confirmed_at is not None


@pytest.mark.asyncio
async def test_a_rejected_proposal_writes_no_mapping(db_session):
    actor, item, control = await _setup(db_session)
    proposal = await _proposal(db_session, item, control)
    await decide(db_session, proposal.id, actor=actor, decision=Decision.REJECT,
                 reason="不成立")
    assert await db_session.scalar(select(Mapping)) is None


@pytest.mark.asyncio
async def test_modified_content_is_revalidated_against_the_framework_item(db_session):
    actor, item, control = await _setup(db_session)
    proposal = await _proposal(db_session, item, control)
    bad = {**proposal.payload, "framework_item_quote": "fabricated wording"}
    with pytest.raises(AppError):
        await decide(db_session, proposal.id, actor=actor, decision=Decision.MODIFY, payload=bad)
    assert await db_session.scalar(select(Mapping)) is None
    await db_session.refresh(proposal)
    assert proposal.status == ProposalStatus.PENDING


@pytest.mark.asyncio
async def test_modifying_strength_is_allowed_and_recorded(db_session):
    actor, item, control = await _setup(db_session)
    proposal = await _proposal(db_session, item, control, strength="full")
    changed = {**proposal.payload, "strength": "supporting"}
    await decide(db_session, proposal.id, actor=actor, decision=Decision.MODIFY, payload=changed)

    mapping = await db_session.scalar(select(Mapping))
    assert mapping.strength == MappingStrength.SUPPORTING
    await db_session.refresh(proposal)
    assert proposal.payload["strength"] == "full"
    assert proposal.decided_payload["strength"] == "supporting"


@pytest.mark.asyncio
async def test_the_same_pair_cannot_be_mapped_twice(db_session):
    from app.errors import Conflict

    actor, item, control = await _setup(db_session)
    first = await _proposal(db_session, item, control)
    await decide(db_session, first.id, actor=actor, decision=Decision.ACCEPT)
    second = await _proposal(db_session, item, control)
    with pytest.raises(Conflict):
        await decide(db_session, second.id, actor=actor, decision=Decision.ACCEPT)
