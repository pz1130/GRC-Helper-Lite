import pytest

from app.controls.models import Control
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.review.models import Proposal, ProposalKind, ProposalStatus


async def _seed(db_session, role: Role, email: str) -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str) -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_contributor_cannot_trigger_inference(client, db_session):
    await _seed(db_session, Role.CONTRIBUTOR, "contrib@example.com")
    headers = await _auth(client, "contrib@example.com")
    assert (await client.post("/api/relations/infer", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_relation_proposals_carry_both_control_statements(client, db_session):
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    left = Control(code="C-0001", title="Approval", statement="Approved by the CAB.")
    right = Control(code="C-0002", title="Implementation", statement="Follow the plan.")
    db_session.add_all([left, right])
    await db_session.flush()
    db_session.add(Proposal(
        kind=ProposalKind.RELATION, status=ProposalStatus.PENDING,
        payload={
            "from_control_id": left.id, "to_control_id": right.id,
            "relation_type": "depends_on", "from_quote": "Approved by the CAB.",
            "to_quote": "Follow the plan.", "rationale": "ordering", "confidence": 0.8,
        },
        citations=[], confidence=0.8,
    ))
    await db_session.flush()

    headers = await _auth(client, "lead@example.com")
    body = (await client.get("/api/proposals?kind=relation", headers=headers)).json()
    context = body[0]["relation_context"]

    assert context["from"]["code"] == "C-0001"
    assert context["from"]["statement"] == "Approved by the CAB."
    assert context["to"]["code"] == "C-0002"
    assert context["relation_type"] == "depends_on"


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence, acceptable", [(0.99, True), (0.8, False)])
async def test_relation_batch_eligibility_respects_threshold(client, db_session, confidence, acceptable):
    """人工选中的关系可批量确认，仍保留置信度阈值。"""
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    left = Control(code="C-0001", title="a", statement="a")
    right = Control(code="C-0002", title="b", statement="b")
    db_session.add_all([left, right])
    await db_session.flush()
    db_session.add(Proposal(
        kind=ProposalKind.RELATION, status=ProposalStatus.PENDING,
        payload={"from_control_id": left.id, "to_control_id": right.id,
                 "relation_type": "duplicates", "from_quote": "a", "to_quote": "b",
                 "rationale": "r", "confidence": confidence},
        citations=[], confidence=confidence,
    ))
    await db_session.flush()

    headers = await _auth(client, "lead@example.com")
    body = (await client.get("/api/proposals?kind=relation", headers=headers)).json()
    assert body[0]["bulk_acceptable"] is acceptable
