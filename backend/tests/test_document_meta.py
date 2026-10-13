from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.ingest.models import DocType, Document


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


async def test_a_reviewer_can_set_the_review_due_date(client, db_session):
    document = await _document(db_session, "Password Policy", 1)
    await _user(db_session, role=Role.GRC_LEAD, email="lead@example.com")
    headers = await _auth(client, "lead@example.com")

    resp = await client.patch(
        f"/api/documents/{document.id}",
        json={"review_due_date": "2027-03-31"},
        headers=headers,
    )

    assert resp.status_code == 200
    assert resp.json()["review_due_date"] == "2027-03-31"


async def test_the_edit_leaves_an_audit_trail(client, db_session):
    from sqlalchemy import select

    from app.iam.models import AuditLog

    document = await _document(db_session, "Password Policy", 2)
    await _user(db_session, role=Role.GRC_LEAD, email="lead@example.com")
    headers = await _auth(client, "lead@example.com")

    await client.patch(
        f"/api/documents/{document.id}",
        json={"review_due_date": "2027-03-31"}, headers=headers)

    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert any("document" in (row.action or "") for row in rows)
    audit = next(row for row in rows if row.action == "document.meta_update")
    assert audit.before == {"review_due_date": None}
    assert audit.after == {"review_due_date": "2027-03-31"}


async def test_parse_output_cannot_be_edited(client, db_session):
    document = await _document(db_session, "Password Policy", 3)
    await _user(db_session, role=Role.GRC_LEAD, email="lead@example.com")
    headers = await _auth(client, "lead@example.com")

    resp = await client.patch(
        f"/api/documents/{document.id}",
        json={"file_hash": "0" * 64}, headers=headers)

    # 白名单之外的字段一律拒绝，不是静默忽略。
    assert resp.status_code == 422


async def test_a_viewer_cannot_edit_metadata(client, db_session):
    document = await _document(db_session, "Password Policy", 4)
    await _user(db_session, role=Role.VIEWER, email="v@example.com")
    headers = await _auth(client, "v@example.com")

    resp = await client.patch(
        f"/api/documents/{document.id}",
        json={"review_due_date": "2027-03-31"}, headers=headers)

    assert resp.status_code == 403


async def test_null_title_is_rejected(client, db_session):
    document = await _document(db_session, "Password Policy", 5)
    await _user(db_session, role=Role.GRC_LEAD, email="lead@example.com")
    headers = await _auth(client, "lead@example.com")

    resp = await client.patch(
        f"/api/documents/{document.id}",
        json={"title": None}, headers=headers)

    assert resp.status_code == 422


async def test_editing_an_unknown_document_is_a_404(client, db_session):
    await _user(db_session, role=Role.GRC_LEAD, email="lead@example.com")
    headers = await _auth(client, "lead@example.com")

    resp = await client.patch(
        "/api/documents/999999", json={"review_due_date": "2027-03-31"}, headers=headers)

    assert resp.status_code == 404
