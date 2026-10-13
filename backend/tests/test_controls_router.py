import pytest

from app.clauses.models import Clause
from app.controls.models import (
    Control,
    ControlRelation,
    ControlSource,
    RelationType,
    SourceRelation,
)
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.ingest.models import DocType, Document


async def _seed(db_session, role: Role, email: str) -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str) -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _control_backed_by_two_documents(db_session) -> Control:
    control = Control(code="C-0001", title="Dual approval", statement="Two approvers.")
    db_session.add(control)
    await db_session.flush()

    for index, (title, number) in enumerate([("Policy", "4"), ("Procedure", "7.2")]):
        doc = Document(
            title=title,
            doc_type=DocType.POLICY,
            file_hash=str(index) * 64,
            file_path="/x.pdf",
            original_filename="x.pdf",
        )
        db_session.add(doc)
        await db_session.flush()
        clause = Clause(
            document_id=doc.id,
            number=number,
            heading="H",
            heading_path=f"{title} › H",
            citation_label=number,
            text="Two approvers.",
            order_index=0,
            level=1,
        )
        db_session.add(clause)
        await db_session.flush()
        db_session.add(
            ControlSource(
                control_id=control.id, clause_id=clause.id, relation=SourceRelation.DEFINES
            )
        )
    await db_session.flush()
    return control


@pytest.mark.asyncio
async def test_viewer_can_list_controls(client, db_session):
    await _seed(db_session, Role.VIEWER, "v@example.com")
    await _control_backed_by_two_documents(db_session)
    headers = await _auth(client, "v@example.com")

    resp = await client.get("/api/controls", headers=headers)
    assert resp.status_code == 200
    assert resp.json()[0]["code"] == "C-0001"


@pytest.mark.asyncio
async def test_search_filters_by_title_and_statement(client, db_session):
    await _seed(db_session, Role.VIEWER, "v@example.com")
    await _control_backed_by_two_documents(db_session)
    headers = await _auth(client, "v@example.com")

    assert len((await client.get("/api/controls?q=approval", headers=headers)).json()) == 1
    assert len((await client.get("/api/controls?q=kubernetes", headers=headers)).json()) == 0


@pytest.mark.asyncio
async def test_detail_shows_every_supporting_document(client, db_session):
    """一个控制点由多份文件支撑——这是 ControlSource 多对多的全部意义。"""
    await _seed(db_session, Role.VIEWER, "v@example.com")
    control = await _control_backed_by_two_documents(db_session)
    headers = await _auth(client, "v@example.com")

    body = (await client.get(f"/api/controls/{control.id}", headers=headers)).json()
    titles = {s["document_title"] for s in body["sources"]}
    assert titles == {"Policy", "Procedure"}
    assert {s["citation_label"] for s in body["sources"]} == {"4", "7.2"}


@pytest.mark.asyncio
async def test_detail_includes_relations(client, db_session):
    await _seed(db_session, Role.VIEWER, "v@example.com")
    first = await _control_backed_by_two_documents(db_session)
    second = Control(code="C-0002", title="Change record", statement="Keep records.")
    db_session.add(second)
    await db_session.flush()
    db_session.add(
        ControlRelation(
            from_control_id=second.id,
            to_control_id=first.id,
            relation_type=RelationType.IMPLEMENTS,
            rationale="…",
        )
    )
    await db_session.flush()
    headers = await _auth(client, "v@example.com")

    body = (await client.get(f"/api/controls/{first.id}", headers=headers)).json()
    assert body["relations"][0]["relation_type"] == "implements"
    # 两端带上编号：页面原来只能显示 #id
    assert (body["relations"][0]["from_control_code"], body["relations"][0]["to_control_code"]) == (
        "C-0002", first.code,
    )


@pytest.mark.asyncio
async def test_viewer_cannot_edit(client, db_session):
    await _seed(db_session, Role.VIEWER, "v@example.com")
    control = await _control_backed_by_two_documents(db_session)
    headers = await _auth(client, "v@example.com")

    resp = await client.patch(f"/api/controls/{control.id}", json={"title": "x"}, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_grc_lead_can_edit_and_it_is_audited(client, db_session):
    from sqlalchemy import select

    from app.iam.models import AuditLog

    await _seed(db_session, Role.GRC_LEAD, "l@example.com")
    control = await _control_backed_by_two_documents(db_session)
    headers = await _auth(client, "l@example.com")

    resp = await client.patch(
        f"/api/controls/{control.id}",
        json={"title": "Dual approval (v2)", "expected": {"title": control.title}},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Dual approval (v2)"
    assert (
        await db_session.scalar(select(AuditLog).where(AuditLog.entity_type == "Control"))
        is not None
    )


@pytest.mark.asyncio
async def test_missing_control_returns_404(client, db_session):
    await _seed(db_session, Role.VIEWER, "v@example.com")
    headers = await _auth(client, "v@example.com")
    assert (await client.get("/api/controls/999999", headers=headers)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,status",
    [
        ({"title": None}, 422),
        ({"statement": " "}, 422),
        ({"owner_user_id": 999999, "expected": {"owner_user_id": None}}, 400),
        ({"unknown": "field"}, 422),
        ({}, 422),
    ],
)
async def test_invalid_patch_returns_client_error(client, db_session, changes, status):
    await _seed(db_session, Role.GRC_LEAD, "l@example.com")
    control = await _control_backed_by_two_documents(db_session)
    headers = await _auth(client, "l@example.com")
    response = await client.patch(f"/api/controls/{control.id}", json=changes, headers=headers)
    assert response.status_code == status


@pytest.mark.asyncio
async def test_patch_audits_all_changed_fields(client, db_session):
    from sqlalchemy import select

    from app.iam.models import AuditLog

    actor = await _seed(db_session, Role.GRC_LEAD, "l@example.com")
    control = await _control_backed_by_two_documents(db_session)
    headers = await _auth(client, "l@example.com")
    response = await client.patch(
        f"/api/controls/{control.id}",
        json={"category": "Access", "owner_user_id": actor.id,
              "expected": {"category": None, "owner_user_id": None}},
        headers=headers,
    )
    assert response.status_code == 200
    audit = await db_session.scalar(select(AuditLog).where(AuditLog.action == "control.update"))
    assert audit.before == {"category": None, "owner_user_id": None}
    assert audit.after == {"category": "Access", "owner_user_id": actor.id}


@pytest.mark.asyncio
async def test_an_edit_made_on_stale_values_is_refused(client, db_session):
    # 两人同时编辑同一个控制点：后保存的原来直接覆盖先保存的，两次都留审计，但后保存
    # 的人不知道自己抹掉了什么。现在页面带上打开编辑时看到的值，库里已经变了就拒绝。
    await _seed(db_session, Role.GRC_LEAD, "l@example.com")
    control = await _control_backed_by_two_documents(db_session)
    headers = await _auth(client, "l@example.com")
    seen = {"title": control.title, "statement": control.statement, "category": control.category}

    first = await client.patch(
        f"/api/controls/{control.id}", headers=headers,
        json={"title": "Dual approval (A)", "expected": seen},
    )
    assert first.status_code == 200

    second = await client.patch(
        f"/api/controls/{control.id}", headers=headers,
        json={"statement": "Rewritten by B.", "expected": seen},
    )
    assert second.status_code == 409
    assert second.json()["code"] == "control.changed_since_loaded"
    assert second.json()["params"] == {"fields": ["title"]}
    await db_session.refresh(control)
    assert control.title == "Dual approval (A)"
    assert control.statement != "Rewritten by B."


@pytest.mark.asyncio
async def test_an_edit_on_current_values_goes_through(client, db_session):
    await _seed(db_session, Role.GRC_LEAD, "l@example.com")
    control = await _control_backed_by_two_documents(db_session)
    headers = await _auth(client, "l@example.com")

    response = await client.patch(
        f"/api/controls/{control.id}", headers=headers,
        json={"category": "Access", "expected": {"title": control.title, "category": control.category}},
    )
    assert response.status_code == 200
    assert response.json()["category"] == "Access"


@pytest.mark.asyncio
async def test_expected_values_alone_are_not_an_edit(client, db_session):
    await _seed(db_session, Role.GRC_LEAD, "l@example.com")
    control = await _control_backed_by_two_documents(db_session)
    headers = await _auth(client, "l@example.com")

    response = await client.patch(
        f"/api/controls/{control.id}", headers=headers, json={"expected": {"title": control.title}},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_an_edit_without_the_values_it_was_based_on_is_refused(client, db_session):
    """并发保护原来在接口上是可选的：页面总会带，脚本不带就能悄悄覆盖别人刚存的（K10）。"""
    await _seed(db_session, Role.GRC_LEAD, "l@example.com")
    control = await _control_backed_by_two_documents(db_session)
    headers = await _auth(client, "l@example.com")

    response = await client.patch(
        f"/api/controls/{control.id}", headers=headers, json={"title": "Overwritten blindly"},
    )
    assert response.status_code == 422
    await db_session.refresh(control)
    assert control.title != "Overwritten blindly"


@pytest.mark.asyncio
async def test_expected_values_must_cover_every_changed_field(client, db_session):
    """只带 `expected: {}`、或只带不相干的字段，同样等于没有保护。"""
    await _seed(db_session, Role.GRC_LEAD, "l@example.com")
    control = await _control_backed_by_two_documents(db_session)
    headers = await _auth(client, "l@example.com")

    for expected in ({}, {"title": control.title}):
        response = await client.patch(
            f"/api/controls/{control.id}", headers=headers,
            json={"statement": "Rewritten.", "expected": expected},
        )
        assert response.status_code == 422, expected
    await db_session.refresh(control)
    assert control.statement != "Rewritten."
