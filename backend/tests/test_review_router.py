from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.clauses.models import Clause
from app.controls.models import Control
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.ingest.models import DocStatus, DocType, Document
from app.review.models import Proposal, ProposalKind


@pytest.fixture(autouse=True)
def embed_queue(monkeypatch):
    enqueue = AsyncMock(return_value="embed-job")
    monkeypatch.setattr("app.review.router.enqueue", enqueue)
    return enqueue


async def _seed(db_session, role: Role, email: str) -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str) -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _proposal(db_session, *, confidence: float = 0.9) -> Proposal:
    from uuid import uuid4

    doc = Document(
        title="P",
        doc_type=DocType.PROCEDURE,
        file_hash=uuid4().hex * 2,
        file_path="/x.pdf",
        original_filename="x.pdf",
        status=DocStatus.ACTIVE,
    )
    db_session.add(doc)
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
    proposal = Proposal(
        kind=ProposalKind.CONTROL_EXTRACT,
        payload={
            "title": "Dual approval",
            "statement": "Two approvers.",
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
async def test_viewer_can_read_the_queue(client, db_session):
    await _seed(db_session, Role.VIEWER, "v@example.com")
    await _proposal(db_session)
    headers = await _auth(client, "v@example.com")

    resp = await client.get("/api/proposals", headers=headers)
    assert resp.status_code == 200
    assert resp.json()[0]["payload"]["title"] == "Dual approval"


@pytest.mark.asyncio
async def test_contributor_cannot_decide(client, db_session):
    """spec D12：确认权只给 GRC Lead 及以上，这是数据质量的守门权。"""
    await _seed(db_session, Role.CONTRIBUTOR, "c@example.com")
    proposal = await _proposal(db_session)
    headers = await _auth(client, "c@example.com")

    resp = await client.post(
        f"/api/proposals/{proposal.id}/decide", json={"decision": "accept"}, headers=headers
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_grc_lead_can_accept(client, db_session, embed_queue):
    await _seed(db_session, Role.GRC_LEAD, "l@example.com")
    proposal = await _proposal(db_session)
    headers = await _auth(client, "l@example.com")

    resp = await client.post(
        f"/api/proposals/{proposal.id}/decide", json={"decision": "accept"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert await db_session.scalar(select(Control)) is not None
    embed_queue.assert_awaited_once_with("embed_controls")


@pytest.mark.asyncio
async def test_modify_requires_a_payload(client, db_session):
    await _seed(db_session, Role.GRC_LEAD, "l@example.com")
    proposal = await _proposal(db_session)
    headers = await _auth(client, "l@example.com")

    resp = await client.post(
        f"/api/proposals/{proposal.id}/decide", json={"decision": "modify"}, headers=headers
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_reject_requires_a_reason(client, db_session):
    """拒绝原因会回流用于改进 prompt，不能省。"""
    await _seed(db_session, Role.GRC_LEAD, "l@example.com")
    proposal = await _proposal(db_session)
    headers = await _auth(client, "l@example.com")

    resp = await client.post(
        f"/api/proposals/{proposal.id}/decide", json={"decision": "reject"}, headers=headers
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bulk_accept_reports_how_many_were_skipped(
    client, db_session, embed_queue
):
    await _seed(db_session, Role.GRC_LEAD, "l@example.com")
    high = await _proposal(db_session, confidence=0.95)
    low = await _proposal(db_session, confidence=0.40)
    headers = await _auth(client, "l@example.com")

    resp = await client.post(
        "/api/proposals/bulk-accept", json={"ids": [high.id, low.id]}, headers=headers
    )
    assert resp.json() == {"accepted": 1, "skipped": 1}
    embed_queue.assert_awaited_once_with("embed_controls")


@pytest.mark.asyncio
async def test_stats_report_pending_counts_by_kind(client, db_session):
    await _seed(db_session, Role.VIEWER, "v@example.com")
    await _proposal(db_session)
    headers = await _auth(client, "v@example.com")

    body = (await client.get("/api/proposals/stats", headers=headers)).json()
    assert body["pending"] == 1
    assert body["by_kind"]["control_extract"] == 1


@pytest.mark.asyncio
async def test_queue_exposes_server_eligibility_and_ocr(client, db_session):
    await _seed(db_session, Role.VIEWER, "v@example.com")
    proposal = await _proposal(db_session, confidence=0.99)
    doc = await db_session.get(Document, proposal.document_id)
    doc.ocr_quality_flag = True
    await db_session.flush()
    headers = await _auth(client, "v@example.com")
    response = await client.get("/api/proposals", headers=headers)
    assert response.json()[0]["ocr_quality_flag"] is True
    assert response.json()[0]["bulk_acceptable"] is False


@pytest.mark.asyncio
async def test_contributor_cannot_bulk_decide(client, db_session):
    await _seed(db_session, Role.CONTRIBUTOR, "c@example.com")
    headers = await _auth(client, "c@example.com")
    response = await client.post("/api/proposals/bulk-accept", json={"ids": [1]}, headers=headers)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_citations_carry_the_source_document_name_and_clause_label(client, db_session):
    """审核人必须看得出这条提案出自哪份规章的哪一条。

    citations 存的是模型原始产出，只有 {clause_id, quote}；界面拿到裸数字
    既判断不了来源，也无从判断引文是否被断章取义。
    """
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    proposal = await _proposal(db_session)
    headers = await _auth(client, "lead@example.com")

    body = (await client.get("/api/proposals", headers=headers)).json()
    citation = next(p for p in body if p["id"] == proposal.id)["citations"][0]

    assert citation["document_title"] == "P"
    assert citation["citation_label"] == "4.1"
    assert citation["heading_path"] == "D › H"
    assert citation["document_id"] == proposal.document_id
    assert citation["quote"] == "Two approvers"          # 原始产出不被改写


@pytest.mark.asyncio
async def test_a_citation_whose_clause_vanished_still_renders(client, db_session):
    """条款被删掉时不能让整页 500——补不上就只保留原始字段。"""
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    proposal = await _proposal(db_session)
    proposal.citations = [{"clause_id": 999999, "quote": "gone"}]
    await db_session.flush()
    headers = await _auth(client, "lead@example.com")

    body = (await client.get("/api/proposals", headers=headers)).json()
    citation = next(p for p in body if p["id"] == proposal.id)["citations"][0]
    assert citation["quote"] == "gone"
    assert citation.get("document_title") is None


async def _mapping_proposal(db_session, strength: str, confidence: float = 0.8) -> Proposal:
    from uuid import uuid4

    from app.frameworks.models import Framework, FrameworkItem

    framework = await db_session.scalar(select(Framework).limit(1))
    if framework is None:
        framework = Framework(key=uuid4().hex[:12], name_zh="z", name_en="e",
                              version="1", source="s", item_count=0)
        db_session.add(framework)
        await db_session.flush()
    item = FrameworkItem(framework_id=framework.id, code=uuid4().hex[:8], title="t",
                         description="Body text.", level=1, order_index=0)
    control = Control(code=f"C-{uuid4().hex[:6]}", title="t", statement="s")
    db_session.add_all([item, control])
    await db_session.flush()
    proposal = Proposal(
        kind=ProposalKind.MAPPING,
        payload={"framework_item_id": item.id, "control_id": control.id,
                 "strength": strength, "framework_item_quote": "Body text.",
                 "rationale": "r", "confidence": confidence},
        citations=[], confidence=confidence,
    )
    db_session.add(proposal)
    await db_session.flush()
    return proposal


@pytest.mark.asyncio
async def test_mapping_proposals_can_be_filtered_by_strength(client, db_session):
    """412 条映射提案里只有 full/partial 影响覆盖度；supporting 不消除差距。

    没有这个过滤，「只过影响结论的那些」就无从下手。
    """
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    full = await _mapping_proposal(db_session, "full")
    partial = await _mapping_proposal(db_session, "partial")
    await _mapping_proposal(db_session, "supporting")
    headers = await _auth(client, "lead@example.com")

    body = (await client.get(
        "/api/proposals?kind=mapping&strength=full&strength=partial", headers=headers)).json()
    assert {p["id"] for p in body} == {full.id, partial.id}

    only_supporting = (await client.get(
        "/api/proposals?kind=mapping&strength=supporting", headers=headers)).json()
    assert {p["payload"]["strength"] for p in only_supporting} == {"supporting"}


@pytest.mark.asyncio
async def test_no_strength_filter_returns_every_strength(client, db_session):
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    for strength in ("full", "partial", "supporting"):
        await _mapping_proposal(db_session, strength)
    headers = await _auth(client, "lead@example.com")

    body = (await client.get("/api/proposals?kind=mapping", headers=headers)).json()
    assert {p["payload"]["strength"] for p in body} == {"full", "partial", "supporting"}


@pytest.mark.asyncio
async def test_an_unknown_strength_is_rejected(client, db_session):
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")
    resp = await client.get("/api/proposals?strength=maybe", headers=headers)
    assert resp.status_code == 422
