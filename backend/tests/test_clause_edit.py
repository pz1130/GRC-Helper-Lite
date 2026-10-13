"""条款拆/合：不删行，留审计。切错的树要有退路，解析器不必完美。"""

import pytest
from sqlalchemy import func, select

from app.clauses.edit import merge_clauses, split_clause
from app.clauses.models import Clause
from app.controls.models import Control, ControlSource, SourceRelation
from app.errors import AppError
from app.iam.models import AuditLog, User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.ingest.models import DocType, Document


async def _user(db_session, role: Role = Role.CONTRIBUTOR, email: str = "c@example.com") -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _doc(db_session, **kwargs) -> Document:
    doc = Document(**{
        "title": "Example Policy",
        "doc_type": DocType.POLICY,
        "file_hash": "a" * 64,
        "file_path": "/data/documents/aa/aaaa.pdf",
        "original_filename": "example.pdf",
        **kwargs,
    })
    db_session.add(doc)
    await db_session.flush()
    return doc


async def _clause(db_session, document: Document, **kwargs) -> Clause:
    clause = Clause(**{
        "document_id": document.id,
        "number": "1",
        "heading": "Scope",
        "heading_path": "Scope",
        "citation_label": "1",
        "text": "First paragraph.\n\nSecond paragraph.",
        "order_index": 0,
        "level": 1,
        "kind": "section",
        **kwargs,
    })
    db_session.add(clause)
    await db_session.flush()
    return clause


@pytest.mark.asyncio
async def test_split_keeps_the_original_row_and_inserts_a_sibling(db_session):
    actor = await _user(db_session)
    document = await _doc(db_session)
    clause = await _clause(db_session, document)
    at = clause.text.index("Second")

    created = await split_clause(db_session, clause.id, at=at, actor=actor)

    await db_session.refresh(clause)
    assert clause.text == "First paragraph."
    assert created.text == "Second paragraph."
    assert created.parent_id == clause.parent_id
    assert created.document_id == document.id
    assert created.order_index == clause.order_index + 1
    assert await db_session.scalar(select(func.count()).select_from(Clause)) == 2
    log = await db_session.scalar(select(AuditLog).where(AuditLog.action == "clause.split"))
    assert log is not None
    assert log.entity_id == str(clause.id)
    assert log.user_id == actor.id


@pytest.mark.asyncio
async def test_split_rejects_an_offset_that_does_not_cut_the_body(db_session):
    actor = await _user(db_session)
    document = await _doc(db_session)
    clause = await _clause(db_session, document, text="short")
    with pytest.raises(AppError, match="切开"):
        await split_clause(db_session, clause.id, at=0, actor=actor)
    with pytest.raises(AppError, match="切开"):
        await split_clause(db_session, clause.id, at=len(clause.text), actor=actor)


@pytest.mark.asyncio
async def test_merge_marks_the_loser_and_does_not_delete_it(db_session):
    actor = await _user(db_session)
    document = await _doc(db_session)
    winner = await _clause(db_session, document, number="1", heading="A", text="Alpha.", order_index=0)
    loser = await _clause(
        db_session, document, number="2", heading="B", heading_path="B",
        citation_label="2", text="Beta.", order_index=1,
    )

    await merge_clauses(db_session, winner_id=winner.id, loser_id=loser.id, actor=actor)

    await db_session.refresh(winner)
    await db_session.refresh(loser)
    assert "Alpha." in winner.text and "Beta." in winner.text
    assert loser.status == "merged"
    assert loser.merged_into_id == winner.id
    assert await db_session.get(Clause, loser.id) is not None
    log = await db_session.scalar(select(AuditLog).where(AuditLog.action == "clause.merge"))
    assert log is not None
    assert log.entity_id == str(loser.id)
    assert log.after["merged_into"] == winner.id


@pytest.mark.asyncio
async def test_merge_moves_control_sources_off_the_loser(db_session):
    actor = await _user(db_session)
    document = await _doc(db_session)
    winner = await _clause(db_session, document, number="1", text="A.", order_index=0)
    loser = await _clause(
        db_session, document, number="2", heading="B", heading_path="B",
        citation_label="2", text="B.", order_index=1,
    )
    control = Control(code="C-0001", title="T", statement="S", status="active")
    db_session.add(control)
    await db_session.flush()
    db_session.add(ControlSource(
        control_id=control.id, clause_id=loser.id, relation=SourceRelation.DEFINES,
    ))
    await db_session.flush()

    await merge_clauses(db_session, winner_id=winner.id, loser_id=loser.id, actor=actor)

    sources = list(await db_session.scalars(select(ControlSource)))
    assert [source.clause_id for source in sources] == [winner.id]


@pytest.mark.asyncio
async def test_merge_rejects_clauses_from_different_documents(db_session):
    actor = await _user(db_session)
    left = await _doc(db_session, file_hash="b" * 64, original_filename="a.pdf")
    right = await _doc(db_session, file_hash="c" * 64, original_filename="b.pdf")
    winner = await _clause(db_session, left, text="A.")
    loser = await _clause(db_session, right, text="B.", heading_path="B", citation_label="1")
    with pytest.raises(AppError, match="同一份"):
        await merge_clauses(db_session, winner_id=winner.id, loser_id=loser.id, actor=actor)


@pytest.mark.asyncio
async def test_split_endpoint_requires_document_write(client, db_session):
    from unittest.mock import AsyncMock, patch

    await _user(db_session, Role.VIEWER, "v@example.com")
    document = await _doc(db_session)
    clause = await _clause(db_session, document)
    token = (
        await client.post("/api/auth/login", json={"email": "v@example.com", "password": "pw123456"})
    ).json()["access_token"]
    with patch("app.clauses.router.enqueue", new=AsyncMock()):
        response = await client.post(
            f"/api/clauses/{clause.id}/split",
            json={"at": 5},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_contributor_can_split_via_the_api(client, db_session):
    from unittest.mock import AsyncMock, patch

    await _user(db_session, Role.CONTRIBUTOR, "c@example.com")
    document = await _doc(db_session)
    clause = await _clause(db_session, document)
    at = clause.text.index("Second")
    token = (
        await client.post("/api/auth/login", json={"email": "c@example.com", "password": "pw123456"})
    ).json()["access_token"]
    with patch("app.clauses.router.enqueue", new=AsyncMock()) as job:
        response = await client.post(
            f"/api/clauses/{clause.id}/split",
            json={"at": at},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["text"] == "Second paragraph."
    job.assert_awaited_once()


@pytest.mark.asyncio
async def test_merge_into_a_descendant_is_rejected_and_the_tree_still_loads(client, db_session):
    # 合并会把败者的子条款挂到胜者名下；胜者若就是败者的后代，它会成为自己（或
    # 自己祖先）的父节点。树里出现环，整份文档的条款树从此加载不出来。
    from unittest.mock import AsyncMock, patch

    await _user(db_session, Role.CONTRIBUTOR, "c@example.com")
    document = await _doc(db_session)
    parent = await _clause(db_session, document, number="1", text="Parent.", order_index=0)
    child = await _clause(
        db_session, document, number="1.1", heading="Child", heading_path="Scope > Child",
        citation_label="1.1", text="Child.", order_index=1, level=2, parent_id=parent.id,
    )
    grandchild = await _clause(
        db_session, document, number="1.1.1", heading="Leaf", heading_path="Scope > Child > Leaf",
        citation_label="1.1.1", text="Leaf.", order_index=2, level=3, parent_id=child.id,
    )
    headers = {"Authorization": "Bearer " + (
        await client.post("/api/auth/login", json={"email": "c@example.com", "password": "pw123456"})
    ).json()["access_token"]}

    with patch("app.clauses.router.enqueue", new=AsyncMock()):
        for winner in (child, grandchild):
            response = await client.post(
                f"/api/clauses/{parent.id}/merge", json={"into_id": winner.id}, headers=headers,
            )
            assert response.status_code == 400
            assert response.json()["code"] == "clause.merge_into_descendant"

    tree = await client.get(f"/api/documents/{document.id}/clauses", headers=headers)
    assert tree.status_code == 200
    assert [node["id"] for node in tree.json()] == [parent.id]
    assert tree.json()[0]["children"][0]["children"][0]["id"] == grandchild.id


@pytest.mark.asyncio
async def test_merge_into_an_ancestor_still_works(db_session):
    # 反方向是正常用法：解析器把一段正文误切成子条款，把它并回父条款。
    actor = await _user(db_session)
    document = await _doc(db_session)
    parent = await _clause(db_session, document, number="1", text="Parent.", order_index=0)
    child = await _clause(
        db_session, document, number="1.1", heading="Child", heading_path="Scope > Child",
        citation_label="1.1", text="Child.", order_index=1, level=2, parent_id=parent.id,
    )

    await merge_clauses(db_session, winner_id=parent.id, loser_id=child.id, actor=actor)

    await db_session.refresh(parent)
    assert parent.text == "Parent.\n\nChild."
    assert parent.parent_id is None


@pytest.mark.asyncio
async def test_a_superseded_version_is_history_and_cannot_be_split_or_merged(db_session):
    # 旧版的条款是变更影响分析比对的那一侧，也是旧控制点出处指着的原文。拆、合它们
    # 等于事后改写历史：影响报告会跟着变，出处引的原文也会变。
    from app.ingest.models import DocStatus

    actor = await _user(db_session)
    document = await _doc(db_session, status=DocStatus.SUPERSEDED)
    first = await _clause(db_session, document, number="1", text="Alpha.\n\nBeta.", order_index=0)
    second = await _clause(
        db_session, document, number="2", heading="B", heading_path="B",
        citation_label="2", text="Gamma.", order_index=1,
    )

    with pytest.raises(AppError) as split:
        await split_clause(db_session, first.id, at=first.text.index("Beta"), actor=actor)
    assert split.value.code == "clause.document_not_editable"
    with pytest.raises(AppError) as merge:
        await merge_clauses(db_session, winner_id=first.id, loser_id=second.id, actor=actor)
    assert merge.value.code == "clause.document_not_editable"

    await db_session.refresh(first)
    await db_session.refresh(second)
    assert first.text == "Alpha.\n\nBeta." and second.status != "merged"
