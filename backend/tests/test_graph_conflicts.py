from app.clauses.models import Clause
from app.conflicts.models import PolicyConflict
from app.controls.models import Control, ControlSource, SourceRelation
from app.graph.service import control_key
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.ingest.models import DocType, Document
from app.review.models import Proposal, ProposalKind, ProposalStatus


async def _user(db_session, role: Role = Role.VIEWER, email: str = "v@example.com") -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str = "v@example.com") -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _document(db_session, title: str, index: int, **extra) -> Document:
    doc = Document(
        title=title, doc_type=DocType.POLICY, file_hash=str(index) * 64,
        file_path=f"/{index}.pdf", original_filename=f"{index}.pdf", **extra,
    )
    db_session.add(doc)
    await db_session.flush()
    return doc


async def _clause(db_session, document_id: int, number: str, text: str, order_index: int = 0) -> Clause:
    clause = Clause(
        document_id=document_id, number=number, heading="H",
        heading_path=f"H › {number}", citation_label=number,
        text=text, order_index=order_index, level=1,
    )
    db_session.add(clause)
    await db_session.flush()
    return clause


async def _control(db_session, code: str, clause_id: int) -> Control:
    """建一个控制点并把它挂到一条条款上。"""
    control = Control(code=code, title=f"Control {code}", statement=f"{code} statement.")
    db_session.add(control)
    await db_session.flush()
    db_session.add(ControlSource(
        control_id=control.id, clause_id=clause_id, relation=SourceRelation.DEFINES))
    await db_session.flush()
    return control


async def _two_documents_in_conflict(db_session):
    """两份文件各一条条款、各一个控制点——冲突派生用例的最小语料。

    返回 (clause_a, clause_b, control_a, control_b)。
    """
    doc_a = await _document(db_session, "Password Policy", 1)
    doc_b = await _document(db_session, "Access Standard", 2)
    clause_a = await _clause(db_session, doc_a.id, "4.2", "Passwords rotate every 90 days.")
    clause_b = await _clause(db_session, doc_b.id, "7.1", "Passwords rotate every 180 days.")
    control_a = await _control(db_session, "C-0001", clause_a.id)
    control_b = await _control(db_session, "C-0002", clause_b.id)
    return clause_a, clause_b, control_a, control_b


async def test_a_confirmed_conflict_becomes_a_red_edge(client, db_session):
    # 控制点 A（文件 1 的条款）与控制点 B（文件 2 的条款）在一处打架
    clause_a, clause_b, control_a, control_b = await _two_documents_in_conflict(db_session)
    await _user(db_session)
    headers = await _auth(client)
    db_session.add(PolicyConflict(
        clause_a_id=clause_a.id, clause_b_id=clause_b.id,
        topic="密码轮换周期", difference="90 vs 180"))
    await db_session.flush()

    resp = await client.get("/api/graph/relations", headers=headers)

    edges = [e for e in resp.json()["edges"] if e["kind"] == "conflicts_with"]
    assert len(edges) == 1
    assert edges[0]["status"] == "confirmed"
    assert {edges[0]["source"], edges[0]["target"]} == {
        control_key(control_a.id), control_key(control_b.id)}


async def test_two_conflicts_between_the_same_controls_collapse_into_one_edge(
    client, db_session
):
    clause_a, clause_b, _control_a, _control_b = await _two_documents_in_conflict(db_session)
    await _user(db_session)
    headers = await _auth(client)
    for topic in ("密码轮换周期", "审批权限"):
        db_session.add(PolicyConflict(
            clause_a_id=clause_a.id, clause_b_id=clause_b.id,
            topic=topic, difference="两处都不一样"))
    await db_session.flush()

    resp = await client.get("/api/graph/relations", headers=headers)

    edges = [e for e in resp.json()["edges"] if e["kind"] == "conflicts_with"]
    assert len(edges) == 1
    # 聚成一条边，但要说清底下有几处
    assert edges[0]["conflict_count"] == 2


async def test_a_conflict_inside_one_control_does_not_draw_a_self_loop(
    client, db_session
):
    # 两条冲突条款都挂在同一个控制点上——自环画不出来，必须跳过而不是崩。
    doc = await _document(db_session, "Password Policy", 1)
    clause_a = await _clause(db_session, doc.id, "4.2", "Rotate every 90 days.")
    clause_b = await _clause(db_session, doc.id, "4.3", "Rotate every 180 days.")
    control = await _control(db_session, "C-0001", clause_a.id)
    db_session.add(ControlSource(
        control_id=control.id, clause_id=clause_b.id, relation=SourceRelation.ELABORATES))
    db_session.add(PolicyConflict(
        clause_a_id=clause_a.id, clause_b_id=clause_b.id,
        topic="密码轮换周期", difference="90 vs 180"))
    await _user(db_session)
    headers = await _auth(client)
    await db_session.flush()
    resp = await client.get("/api/graph/relations", headers=headers)

    assert resp.status_code == 200
    assert [e for e in resp.json()["edges"] if e["kind"] == "conflicts_with"] == []


async def test_pending_conflict_proposals_show_as_dashed_edges(client, db_session):
    clause_a, clause_b, _control_a, _control_b = await _two_documents_in_conflict(db_session)
    await _user(db_session)
    headers = await _auth(client)
    db_session.add(Proposal(
        kind=ProposalKind.CONFLICT, status=ProposalStatus.PENDING, citations=[],
        payload={"clause_a_id": clause_a.id, "clause_b_id": clause_b.id,
                 "topic": "密码轮换周期", "difference": "90 vs 180",
                 "quote_a": "q", "quote_b": "q", "confidence": 0.8}))
    await db_session.flush()

    resp = await client.get(
        "/api/graph/relations?include_pending=true", headers=headers)

    edges = [e for e in resp.json()["edges"] if e["kind"] == "conflicts_with"]
    assert len(edges) == 1 and edges[0]["status"] == "pending"


async def test_pending_conflicts_are_hidden_when_pending_is_off(client, db_session):
    clause_a, clause_b, _control_a, _control_b = await _two_documents_in_conflict(db_session)
    await _user(db_session)
    headers = await _auth(client)
    db_session.add(Proposal(
        kind=ProposalKind.CONFLICT, status=ProposalStatus.PENDING, citations=[],
        payload={"clause_a_id": clause_a.id, "clause_b_id": clause_b.id,
                 "topic": "密码轮换周期", "difference": "90 vs 180",
                 "quote_a": "q", "quote_b": "q", "confidence": 0.8}))
    await db_session.flush()

    resp = await client.get("/api/graph/relations", headers=headers)

    assert [e for e in resp.json()["edges"] if e["kind"] == "conflicts_with"] == []
