import pytest

from app.clauses.models import Clause
from app.controls.models import (
    Control,
    ControlRelation,
    ControlSource,
    RelationType,
    SourceRelation,
)
from app.frameworks.models import Framework, FrameworkItem, Mapping, MappingStrength
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


async def _document(db_session, title: str, index: int) -> Document:
    doc = Document(
        title=title,
        doc_type=DocType.POLICY,
        file_hash=str(index) * 64,
        file_path=f"/{index}.pdf",
        original_filename=f"{index}.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    return doc


async def _clause(db_session, document_id: int, number: str, order_index: int = 0) -> Clause:
    clause = Clause(
        document_id=document_id,
        number=number,
        heading="H",
        heading_path=f"H › {number}",
        citation_label=number,
        text=f"Clause {number} text.",
        order_index=order_index,
        level=1,
    )
    db_session.add(clause)
    await db_session.flush()
    return clause


async def _control(db_session, code: str, title: str = "T") -> Control:
    control = Control(code=code, title=title, statement=f"{code} statement.")
    db_session.add(control)
    await db_session.flush()
    return control


async def _relation(db_session, src: Control, dst: Control, kind: RelationType) -> ControlRelation:
    relation = ControlRelation(
        from_control_id=src.id,
        to_control_id=dst.id,
        relation_type=kind,
        rationale="because",
        confidence=0.9,
    )
    db_session.add(relation)
    await db_session.flush()
    return relation


@pytest.mark.asyncio
async def test_panorama_returns_controls_and_confirmed_relations(client, db_session):
    await _user(db_session)
    first = await _control(db_session, "C-0001")
    second = await _control(db_session, "C-0002")
    await _relation(db_session, first, second, RelationType.DEPENDS_ON)
    headers = await _auth(client)

    resp = await client.get("/api/graph/relations", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert [n["key"] for n in body["nodes"]] == [f"control:{first.id}", f"control:{second.id}"]
    assert body["nodes"][0]["kind"] == "control"
    assert body["nodes"][0]["code"] == "C-0001"
    assert len(body["edges"]) == 1
    assert body["edges"][0]["source"] == f"control:{first.id}"
    assert body["edges"][0]["target"] == f"control:{second.id}"
    assert body["edges"][0]["kind"] == "depends_on"
    assert body["edges"][0]["status"] == "confirmed"
    assert body["edges"][0]["proposal_id"] is None
    assert body["stats"] == {"nodes": 2, "edges": 1, "pending_edges": 0, "truncated": False}


@pytest.mark.asyncio
async def test_graph_requires_a_token(client, db_session):
    await _user(db_session)
    resp = await client.get("/api/graph/relations")
    assert resp.status_code == 401


async def _chain(db_session) -> list[Control]:
    """C-0001 → C-0002 → C-0003，外加一个孤立的 C-0009。"""
    first = await _control(db_session, "C-0001")
    second = await _control(db_session, "C-0002")
    third = await _control(db_session, "C-0003")
    await _control(db_session, "C-0009")
    await _relation(db_session, first, second, RelationType.DEPENDS_ON)
    await _relation(db_session, second, third, RelationType.DEPENDS_ON)
    return [first, second, third]


@pytest.mark.asyncio
async def test_focus_on_a_control_returns_one_hop_neighbourhood(client, db_session):
    await _user(db_session)
    first, second, _third = await _chain(db_session)
    headers = await _auth(client)

    resp = await client.get(
        f"/api/graph/relations?focus=control:{first.id}&hops=1", headers=headers
    )

    assert resp.status_code == 200
    body = resp.json()
    assert {n["key"] for n in body["nodes"]} == {control_key(first.id), control_key(second.id)}
    assert len(body["edges"]) == 1


@pytest.mark.asyncio
async def test_two_hops_reaches_further(client, db_session):
    await _user(db_session)
    first, second, third = await _chain(db_session)
    headers = await _auth(client)

    resp = await client.get(
        f"/api/graph/relations?focus=control:{first.id}&hops=2", headers=headers
    )

    body = resp.json()
    assert {n["key"] for n in body["nodes"]} == {
        control_key(first.id),
        control_key(second.id),
        control_key(third.id),
    }
    assert len(body["edges"]) == 2


@pytest.mark.asyncio
async def test_focus_on_a_document_seeds_every_control_it_defines(client, db_session):
    await _user(db_session)
    doc = await _document(db_session, "Change Management", 1)
    clause = await _clause(db_session, doc.id, "4.2")
    first = await _control(db_session, "C-0001")
    second = await _control(db_session, "C-0002")
    db_session.add(
        ControlSource(control_id=first.id, clause_id=clause.id, relation=SourceRelation.DEFINES)
    )
    await _relation(db_session, first, second, RelationType.DUPLICATES)
    await db_session.flush()
    headers = await _auth(client)

    resp = await client.get(f"/api/graph/relations?focus=document:{doc.id}&hops=1", headers=headers)

    body = resp.json()
    assert {n["key"] for n in body["nodes"]} == {control_key(first.id), control_key(second.id)}


@pytest.mark.asyncio
async def test_bad_focus_is_rejected(client, db_session):
    await _user(db_session)
    headers = await _auth(client)
    resp = await client.get("/api/graph/relations?focus=banana", headers=headers)
    assert resp.status_code == 400


async def _relation_proposal(
    db_session,
    src: Control,
    dst: Control,
    kind: RelationType,
    status: ProposalStatus = ProposalStatus.PENDING,
) -> Proposal:
    proposal = Proposal(
        kind=ProposalKind.RELATION,
        status=status,
        confidence=0.8,
        payload={
            "from_control_id": src.id,
            "to_control_id": dst.id,
            "relation_type": kind.value,
            "rationale": "model said so",
            "confidence": 0.8,
        },
        citations=[],
    )
    db_session.add(proposal)
    await db_session.flush()
    return proposal


@pytest.mark.asyncio
async def test_pending_proposals_are_excluded_by_default(client, db_session):
    await _user(db_session)
    first = await _control(db_session, "C-0001")
    second = await _control(db_session, "C-0002")
    await _relation_proposal(db_session, first, second, RelationType.DEPENDS_ON)
    headers = await _auth(client)

    resp = await client.get("/api/graph/relations", headers=headers)

    body = resp.json()
    assert body["edges"] == []
    assert body["stats"]["pending_edges"] == 0


@pytest.mark.asyncio
async def test_pending_proposals_are_drawn_and_counted_separately(client, db_session):
    await _user(db_session)
    first = await _control(db_session, "C-0001")
    second = await _control(db_session, "C-0002")
    third = await _control(db_session, "C-0003")
    await _relation(db_session, first, second, RelationType.DEPENDS_ON)
    proposal = await _relation_proposal(db_session, second, third, RelationType.DUPLICATES)
    headers = await _auth(client)

    resp = await client.get("/api/graph/relations?include_pending=true", headers=headers)

    body = resp.json()
    pending = [e for e in body["edges"] if e["status"] == "pending"]
    assert len(pending) == 1
    assert pending[0]["key"] == f"proposal:{proposal.id}"
    assert pending[0]["proposal_id"] == proposal.id
    assert pending[0]["kind"] == "duplicates"
    assert body["stats"] == {"nodes": 3, "edges": 2, "pending_edges": 1, "truncated": False}
    by_key = {n["key"]: n for n in body["nodes"]}
    assert by_key[control_key(second.id)]["pending_edges"] == 1
    assert by_key[control_key(first.id)]["pending_edges"] == 0


@pytest.mark.asyncio
async def test_decided_proposals_never_appear(client, db_session):
    await _user(db_session)
    first = await _control(db_session, "C-0001")
    second = await _control(db_session, "C-0002")
    await _relation_proposal(
        db_session, first, second, RelationType.DEPENDS_ON, status=ProposalStatus.ACCEPTED
    )
    await _relation_proposal(
        db_session, second, first, RelationType.DEPENDS_ON, status=ProposalStatus.REJECTED
    )
    headers = await _auth(client)

    resp = await client.get("/api/graph/relations?include_pending=true", headers=headers)

    assert resp.json()["edges"] == []


@pytest.mark.asyncio
async def test_a_confirmed_edge_beats_a_pending_proposal_for_the_same_pair(client, db_session):
    await _user(db_session)
    first = await _control(db_session, "C-0001")
    second = await _control(db_session, "C-0002")
    await _relation(db_session, first, second, RelationType.DUPLICATES)
    # 同一对、同一类型，只是方向相反——duplicates 无方向，不该画成两条。
    await _relation_proposal(db_session, second, first, RelationType.DUPLICATES)
    headers = await _auth(client)

    resp = await client.get("/api/graph/relations?include_pending=true", headers=headers)

    body = resp.json()
    assert len(body["edges"]) == 1
    assert body["edges"][0]["status"] == "confirmed"
    assert body["stats"]["pending_edges"] == 0


@pytest.mark.asyncio
async def test_two_hops_may_shorten_once_pending_edges_join(client, db_session):
    await _user(db_session)
    first = await _control(db_session, "C-0001")
    second = await _control(db_session, "C-0002")
    third = await _control(db_session, "C-0003")
    await _relation(db_session, first, second, RelationType.DEPENDS_ON)
    await _relation(db_session, second, third, RelationType.DEPENDS_ON)
    await _relation_proposal(db_session, first, third, RelationType.DEPENDS_ON)
    headers = await _auth(client)

    resp = await client.get(
        f"/api/graph/relations?focus=control:{first.id}&hops=1&include_pending=true",
        headers=headers,
    )

    assert {n["key"] for n in resp.json()["nodes"]} == {
        control_key(first.id),
        control_key(second.id),
        control_key(third.id),
    }


async def _framework(db_session, key: str = "nist-csf-2.0") -> tuple[Framework, FrameworkItem]:
    framework = Framework(
        key=key, name_zh="框架", name_en="Framework", version="2.0", source="nist.gov"
    )
    db_session.add(framework)
    await db_session.flush()
    function = FrameworkItem(
        framework_id=framework.id, parent_id=None, code="GV", title="Govern", level=1, order_index=0
    )
    db_session.add(function)
    await db_session.flush()
    leaf = FrameworkItem(
        framework_id=framework.id,
        parent_id=function.id,
        code="GV.PO-01",
        title="Policy",
        level=2,
        order_index=1,
    )
    db_session.add(leaf)
    await db_session.flush()
    return framework, leaf


@pytest.mark.asyncio
async def test_group_prefers_the_defining_document_and_lists_the_rest(client, db_session):
    await _user(db_session)
    elaborating = await _document(db_session, "Procedure", 1)
    defining = await _document(db_session, "Policy", 2)
    control = await _control(db_session, "C-0001")
    first_clause = await _clause(db_session, elaborating.id, "7.2")
    second_clause = await _clause(db_session, defining.id, "4.1")
    db_session.add(
        ControlSource(
            control_id=control.id, clause_id=first_clause.id, relation=SourceRelation.ELABORATES
        )
    )
    db_session.add(
        ControlSource(
            control_id=control.id, clause_id=second_clause.id, relation=SourceRelation.DEFINES
        )
    )
    await db_session.flush()
    headers = await _auth(client)

    body = (await client.get("/api/graph/relations", headers=headers)).json()

    node = body["nodes"][0]
    assert node["group"] == f"document:{defining.id}"
    assert node["group_extra"] == [f"document:{elaborating.id}"]
    assert {g["key"]: g["label"] for g in body["groups"]} == {
        f"document:{defining.id}": "Policy",
        f"document:{elaborating.id}": "Procedure",
    }
    assert {g["kind"] for g in body["groups"]} == {"document"}


@pytest.mark.asyncio
async def test_functions_come_from_the_level_one_ancestor_of_each_mapping(client, db_session):
    await _user(db_session)
    _framework_row, leaf = await _framework(db_session)
    mapped = await _control(db_session, "C-0001")
    await _control(db_session, "C-0002")
    db_session.add(
        Mapping(
            control_id=mapped.id,
            framework_item_id=leaf.id,
            strength=MappingStrength.FULL,
            rationale="",
            quote="",
        )
    )
    await db_session.flush()
    headers = await _auth(client)

    body = (await client.get("/api/graph/relations", headers=headers)).json()

    by_key = {n["key"]: n for n in body["nodes"]}
    assert by_key[control_key(mapped.id)]["functions"] == ["GV"]
    assert [n["functions"] for n in body["nodes"] if n["code"] == "C-0002"] == [[]]


@pytest.mark.asyncio
async def test_type_filter_keeps_only_the_requested_relation_types(client, db_session):
    await _user(db_session)
    first = await _control(db_session, "C-0001")
    second = await _control(db_session, "C-0002")
    third = await _control(db_session, "C-0003")
    await _relation(db_session, first, second, RelationType.DEPENDS_ON)
    await _relation(db_session, second, third, RelationType.DUPLICATES)
    headers = await _auth(client)

    body = (await client.get("/api/graph/relations?types=duplicates", headers=headers)).json()

    assert [e["kind"] for e in body["edges"]] == ["duplicates"]


@pytest.mark.asyncio
async def test_framework_filter_keeps_only_controls_mapped_into_it(client, db_session):
    await _user(db_session)
    framework, leaf = await _framework(db_session)
    mapped = await _control(db_session, "C-0001")
    await _control(db_session, "C-0002")
    db_session.add(
        Mapping(
            control_id=mapped.id,
            framework_item_id=leaf.id,
            strength=MappingStrength.PARTIAL,
            rationale="",
            quote="",
        )
    )
    await db_session.flush()
    headers = await _auth(client)

    body = (
        await client.get(f"/api/graph/relations?framework_id={framework.id}", headers=headers)
    ).json()

    assert [n["code"] for n in body["nodes"]] == ["C-0001"]


@pytest.mark.asyncio
async def test_panorama_truncation_is_deterministic(client, db_session, monkeypatch):
    from app.graph import service as graph_service

    monkeypatch.setattr(graph_service, "MAX_NODES", 2)
    await _user(db_session)
    for index in range(4):
        await _control(db_session, f"C-000{index}")
    headers = await _auth(client)

    first = (await client.get("/api/graph/relations", headers=headers)).json()
    second = (await client.get("/api/graph/relations", headers=headers)).json()

    assert [n["code"] for n in first["nodes"]] == ["C-0000", "C-0001"]
    assert first["stats"]["truncated"] is True
    assert [n["code"] for n in second["nodes"]] == [n["code"] for n in first["nodes"]]


@pytest.mark.asyncio
async def test_mapping_graph_is_bipartite_and_marks_gaps(client, db_session):
    await _user(db_session)
    framework, leaf = await _framework(db_session)
    gap = FrameworkItem(
        framework_id=framework.id,
        parent_id=None,
        code="PR.AA-01",
        title="Access",
        level=2,
        order_index=2,
    )
    db_session.add(gap)
    await db_session.flush()
    control = await _control(db_session, "C-0001")
    db_session.add(
        Mapping(
            control_id=control.id,
            framework_item_id=leaf.id,
            strength=MappingStrength.SUPPORTING,
            rationale="partly",
            quote="stakeholders",
        )
    )
    await db_session.flush()
    headers = await _auth(client)

    body = (
        await client.get(f"/api/graph/mappings?framework_id={framework.id}", headers=headers)
    ).json()

    kinds = {n["key"]: n["kind"] for n in body["nodes"]}
    assert kinds[f"control:{control.id}"] == "control"
    assert kinds[f"item:{leaf.id}"] == "framework_item"
    gaps = [n for n in body["nodes"] if n["is_gap"]]
    assert [n["code"] for n in gaps] == ["PR.AA-01"]
    assert len(body["edges"]) == 1
    assert body["edges"][0]["kind"] == "supporting"
    assert body["edges"][0]["source"] == f"control:{control.id}"
    assert body["edges"][0]["target"] == f"item:{leaf.id}"


@pytest.mark.asyncio
async def test_mapping_graph_can_show_only_gaps(client, db_session):
    await _user(db_session)
    framework, leaf = await _framework(db_session)
    gap = FrameworkItem(
        framework_id=framework.id,
        parent_id=None,
        code="PR.AA-01",
        title="Access",
        level=2,
        order_index=2,
    )
    db_session.add(gap)
    await db_session.flush()
    control = await _control(db_session, "C-0001")
    db_session.add(
        Mapping(
            control_id=control.id,
            framework_item_id=leaf.id,
            strength=MappingStrength.FULL,
            rationale="",
            quote="",
        )
    )
    await db_session.flush()
    headers = await _auth(client)

    body = (
        await client.get(
            f"/api/graph/mappings?framework_id={framework.id}&only_gaps=true", headers=headers
        )
    ).json()

    assert [n["code"] for n in body["nodes"]] == ["PR.AA-01"]
    assert body["edges"] == []


@pytest.mark.asyncio
async def test_mapping_graph_draws_pending_mapping_proposals(client, db_session):
    await _user(db_session)
    framework, leaf = await _framework(db_session)
    control = await _control(db_session, "C-0001")
    proposal = Proposal(
        kind=ProposalKind.MAPPING,
        status=ProposalStatus.PENDING,
        confidence=0.95,
        payload={
            "control_id": control.id,
            "framework_item_id": leaf.id,
            "strength": "partial",
            "rationale": "supports stakeholder discovery",
            "confidence": 0.95,
        },
        citations=[],
    )
    db_session.add(proposal)
    await db_session.flush()
    headers = await _auth(client)

    body = (
        await client.get(
            f"/api/graph/mappings?framework_id={framework.id}&include_pending=true", headers=headers
        )
    ).json()

    pending = [e for e in body["edges"] if e["status"] == "pending"]
    assert len(pending) == 1
    assert pending[0]["kind"] == "partial"
    assert pending[0]["proposal_id"] == proposal.id
    # 有待确认连线的框架项不算差距，但它也还没被覆盖——is_gap 只看已确认的线。
    assert [n["is_gap"] for n in body["nodes"] if n["key"] == f"item:{leaf.id}"] == [True]


@pytest.mark.asyncio
async def test_mapping_graph_focus_on_an_item_keeps_its_controls(client, db_session):
    await _user(db_session)
    framework, leaf = await _framework(db_session)
    other = FrameworkItem(
        framework_id=framework.id,
        parent_id=None,
        code="PR.AA-01",
        title="Access",
        level=2,
        order_index=2,
    )
    db_session.add(other)
    await db_session.flush()
    first = await _control(db_session, "C-0001")
    second = await _control(db_session, "C-0002")
    db_session.add(
        Mapping(
            control_id=first.id,
            framework_item_id=leaf.id,
            strength=MappingStrength.FULL,
            rationale="",
            quote="",
        )
    )
    db_session.add(
        Mapping(
            control_id=second.id,
            framework_item_id=other.id,
            strength=MappingStrength.FULL,
            rationale="",
            quote="",
        )
    )
    await db_session.flush()
    headers = await _auth(client)

    body = (
        await client.get(
            f"/api/graph/mappings?framework_id={framework.id}&focus=item:{leaf.id}&hops=1",
            headers=headers,
        )
    ).json()

    assert {n["key"] for n in body["nodes"]} == {f"item:{leaf.id}", f"control:{first.id}"}


@pytest.mark.asyncio
async def test_a_single_clause_can_be_read_for_the_drawer(client, db_session):
    await _user(db_session)
    doc = await _document(db_session, "Incident Management", 1)
    clause = await _clause(db_session, doc.id, "3.4.2")
    headers = await _auth(client)

    resp = await client.get(f"/api/clauses/{clause.id}", headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {
        "id": clause.id,
        "document_id": doc.id,
        "document_title": "Incident Management",
        "number": "3.4.2",
        "heading": "H",
        "heading_path": "H › 3.4.2",
        "citation_label": "3.4.2",
        "text": "Clause 3.4.2 text.",
        "level": 1,
        "page_ref": None,
    }


@pytest.mark.asyncio
async def test_missing_clause_is_404(client, db_session):
    await _user(db_session)
    headers = await _auth(client)
    assert (await client.get("/api/clauses/999999", headers=headers)).status_code == 404


