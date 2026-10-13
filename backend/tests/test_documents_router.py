import io
from unittest.mock import AsyncMock, patch

import pytest
from docx import Document as DocxDocument

from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password


async def _seed(db_session, role: Role, email: str) -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", json={"email": email, "password": "pw123456"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _docx_bytes() -> bytes:
    doc = DocxDocument()
    doc.add_heading("Introduction", level=1)
    doc.add_paragraph("Purpose.")
    doc.add_heading("Roles and Responsibilities", level=1)
    doc.add_paragraph("IT Division.")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_upload_requires_document_write(client, db_session):
    await _seed(db_session, Role.VIEWER, "v@example.com")
    headers = await _auth(client, "v@example.com")

    response = await client.post(
        "/api/documents",
        files={"file": ("g.docx", _docx_bytes())},
        data={"title": "Guideline", "doc_type": "guideline"},
        headers=headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_upload_enqueues_a_parse_job(client, db_session):
    await _seed(db_session, Role.CONTRIBUTOR, "c@example.com")
    headers = await _auth(client, "c@example.com")

    with patch("app.ingest.router.enqueue", new=AsyncMock(return_value="job-1")) as job:
        response = await client.post(
            "/api/documents",
            files={"file": ("g.docx", _docx_bytes())},
            data={"title": "Guideline", "doc_type": "guideline"},
            headers=headers,
        )
    assert response.status_code == 201
    assert response.json()["status"] == "uploaded"
    job.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_upload_is_rejected_with_conflict(client, db_session):
    await _seed(db_session, Role.CONTRIBUTOR, "c@example.com")
    headers = await _auth(client, "c@example.com")
    payload = _docx_bytes()

    with patch("app.ingest.router.enqueue", new=AsyncMock(return_value="job-1")):
        first = await client.post(
            "/api/documents",
            files={"file": ("g.docx", payload)},
            data={"title": "G", "doc_type": "guideline"},
            headers=headers,
        )
        second = await client.post(
            "/api/documents",
            files={"file": ("g-again.docx", payload)},
            data={"title": "G", "doc_type": "guideline"},
            headers=headers,
        )
    assert first.status_code == 201
    assert second.status_code == 409
    assert "已存在" in second.json()["message"]


@pytest.mark.asyncio
async def test_oversized_upload_is_rejected(client, db_session, monkeypatch):
    await _seed(db_session, Role.CONTRIBUTOR, "c@example.com")
    headers = await _auth(client, "c@example.com")
    monkeypatch.setattr("app.ingest.router.MAX_UPLOAD_BYTES", 16)

    response = await client.post(
        "/api/documents",
        files={"file": ("g.docx", _docx_bytes())},
        data={"title": "Guideline", "doc_type": "guideline"},
        headers=headers,
    )
    assert response.status_code == 413
    assert "过大" in response.json()["message"]


@pytest.mark.asyncio
async def test_unsupported_extension_is_rejected(client, db_session):
    await _seed(db_session, Role.CONTRIBUTOR, "c@example.com")
    headers = await _auth(client, "c@example.com")

    response = await client.post(
        "/api/documents",
        files={"file": ("matrix.xlsx", b"x")},
        data={"title": "M", "doc_type": "standard"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "xlsx" in response.json()["message"]


@pytest.mark.asyncio
async def test_upload_is_audited(client, db_session):
    await _seed(db_session, Role.ADMIN, "a@example.com")
    headers = await _auth(client, "a@example.com")

    with patch("app.ingest.router.enqueue", new=AsyncMock(return_value="job-1")):
        await client.post(
            "/api/documents",
            files={"file": ("g.docx", _docx_bytes())},
            data={"title": "Guideline", "doc_type": "guideline"},
            headers=headers,
        )
    log = await client.get("/api/audit-log?entity_type=Document", headers=headers)
    assert any(entry["action"] == "document.upload" for entry in log.json())


@pytest.mark.asyncio
async def test_clause_tree_is_nested(client, db_session):
    from app.clauses.models import Clause
    from app.ingest.models import DocType, Document

    await _seed(db_session, Role.VIEWER, "v@example.com")
    headers = await _auth(client, "v@example.com")

    doc = Document(
        title="P",
        doc_type=DocType.PROCEDURE,
        file_hash="a" * 64,
        file_path="/x.pdf",
        original_filename="x.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    parent = Clause(
        document_id=doc.id,
        number="4",
        heading="Process",
        heading_path="Process",
        citation_label="4",
        text="",
        order_index=0,
        level=1,
    )
    db_session.add(parent)
    await db_session.flush()
    db_session.add(
        Clause(
            document_id=doc.id,
            parent_id=parent.id,
            number="4.1",
            heading="Normal",
            heading_path="Process › Normal",
            citation_label="4.1",
            text="body",
            order_index=1,
            level=2,
        )
    )
    await db_session.flush()

    response = await client.get(f"/api/documents/{doc.id}/clauses", headers=headers)
    tree = response.json()
    assert len(tree) == 1
    assert tree[0]["citation_label"] == "4"
    assert tree[0]["children"][0]["citation_label"] == "4.1"


@pytest.mark.asyncio
async def test_plain_text_fallback_recovers_a_failed_document(client, db_session, monkeypatch):
    from app.indexing.service import rebuild_chunks
    from app.ingest.models import DocStatus, DocType, Document
    from app.search.fulltext import search

    job = AsyncMock(return_value="index-job")
    monkeypatch.setattr("app.ingest.router.enqueue", job)

    await _seed(db_session, Role.CONTRIBUTOR, "c@example.com")
    headers = await _auth(client, "c@example.com")

    doc = Document(
        title="P",
        doc_type=DocType.PROCEDURE,
        file_hash="b" * 64,
        file_path="/x.pdf",
        original_filename="x.pdf",
        status=DocStatus.PARSE_FAILED,
        parse_error="加密的 PDF",
    )
    db_session.add(doc)
    await db_session.flush()

    response = await client.post(
        f"/api/documents/{doc.id}/plain-text",
        json={"text": "1 Scope\nThis procedure applies to all systems.\n2 Purpose\nDefine it."},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "active"
    job.assert_awaited_once_with("index_document", doc.id)

    tree = await client.get(f"/api/documents/{doc.id}/clauses", headers=headers)
    assert [node["citation_label"] for node in tree.json()] == ["1", "2"]
    await rebuild_chunks(db_session, document_id=doc.id)
    assert await search(db_session, "all systems", document_id=doc.id)


@pytest.mark.asyncio
async def test_document_list_is_readable_by_viewer(client, db_session):
    await _seed(db_session, Role.VIEWER, "v@example.com")
    headers = await _auth(client, "v@example.com")
    assert (await client.get("/api/documents", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_reparse_marks_document_as_queued(client, db_session):
    from app.ingest.models import DocStatus, DocType, Document

    await _seed(db_session, Role.CONTRIBUTOR, "retry@example.com")
    headers = await _auth(client, "retry@example.com")
    doc = Document(
        title="Retry", doc_type=DocType.STANDARD, file_hash="c" * 64,
        file_path="/retry.docx", original_filename="retry.docx",
        status=DocStatus.PARSE_FAILED, parse_error="Previous failure",
        parse_error_code="parse_error.docx_unreadable", parse_error_params={"error": "BadZipFile"},
    )
    db_session.add(doc)
    await db_session.flush()
    with patch("app.ingest.router.enqueue", new=AsyncMock(return_value="retry-job")) as job:
        response = await client.post(f"/api/documents/{doc.id}/reparse", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "uploaded"
    assert response.json()["parse_error"] is None
    assert response.json()["parse_error_code"] is None
    job.assert_awaited_once_with("parse_document", doc.id)


async def _document_with_confirmed_source(db_session, *, status):
    """一份文档，它的一条条款已经是某个已确认控制点的出处。"""
    from app.clauses.models import Clause
    from app.controls.models import Control, ControlSource, SourceRelation
    from app.ingest.models import DocType, Document

    doc = Document(
        title="Linked", doc_type=DocType.POLICY, file_hash="d" * 64,
        file_path="/linked.docx", original_filename="linked.docx", status=status,
    )
    db_session.add(doc)
    await db_session.flush()
    clause = Clause(
        document_id=doc.id, number="1", heading="Scope", heading_path="Scope",
        citation_label="1", text="Access shall be reviewed.", order_index=0, level=1,
    )
    control = Control(code="AC-01", title="Access review")
    db_session.add_all([clause, control])
    await db_session.flush()
    db_session.add(
        ControlSource(control_id=control.id, clause_id=clause.id, relation=SourceRelation.DEFINES)
    )
    await db_session.flush()
    return doc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body"),
    [("reparse", None), ("plain-text", {"text": "1 Scope\nAccess shall be reviewed."})],
)
async def test_rewriting_clauses_refuses_when_confirmed_data_points_at_them(
    client, db_session, path, body
):
    # 重新解析和粘贴兜底都先删光本文档的条款再重建。control_sources 与
    # policy_conflicts 对条款是 ON DELETE CASCADE，所以已确认的出处会被数据库
    # 悄悄删掉：绕过 materialize，也不留审计。有引用就必须拒绝。
    from sqlalchemy import func, select

    from app.controls.models import ControlSource
    from app.ingest.models import DocStatus

    await _seed(db_session, Role.CONTRIBUTOR, "linked@example.com")
    headers = await _auth(client, "linked@example.com")
    doc = await _document_with_confirmed_source(db_session, status=DocStatus.PARSE_FAILED)

    with patch("app.ingest.router.enqueue", new=AsyncMock()) as job:
        response = await client.post(
            f"/api/documents/{doc.id}/{path}", json=body, headers=headers
        )

    assert response.status_code == 409
    assert response.json()["code"] == "document.clauses_referenced"
    assert response.json()["params"] == {"sources": 1, "conflicts": 0}
    job.assert_not_awaited()
    assert await db_session.scalar(select(func.count()).select_from(ControlSource)) == 1


@pytest.mark.asyncio
async def test_upload_superseding_a_missing_document_is_a_not_found(client, db_session):
    # 外键会拦下它，但拦成的是 IntegrityError——用户看到 500，而且文件已经落进
    # 暂存区。先查，给出一个说得清的错误。
    await _seed(db_session, Role.CONTRIBUTOR, "sup@example.com")
    headers = await _auth(client, "sup@example.com")

    with patch("app.ingest.router.enqueue", new=AsyncMock()) as job:
        response = await client.post(
            "/api/documents",
            files={"file": ("g.docx", _docx_bytes())},
            data={"title": "Guideline v2", "doc_type": "guideline", "supersedes_id": "999999"},
            headers=headers,
        )
    assert response.status_code == 404
    assert response.json()["code"] == "document.previous_version_not_found"
    job.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_superseding_an_already_superseded_version_is_rejected(client, db_session):
    # v1 已被 v2 替换，再给 v1 传一个「新版本」v3：v3 解析成功时只会去撤 v1（早已
    # 撤下，跳过），v2 没人撤——v2、v3 同时是现行版本，一起参与抽取、冲突检测、
    # 审计答复检索。新版本只能接在现行那一版后面。
    from sqlalchemy import func, select

    from app.ingest.models import DocStatus, DocType, Document

    await _seed(db_session, Role.CONTRIBUTOR, "old@example.com")
    headers = await _auth(client, "old@example.com")
    v1 = Document(
        title="P v1", doc_type=DocType.POLICY, file_hash="f" * 64,
        file_path="/v1.pdf", original_filename="v1.pdf", status=DocStatus.SUPERSEDED,
    )
    db_session.add(v1)
    await db_session.flush()

    with patch("app.ingest.router.enqueue", new=AsyncMock()) as job:
        response = await client.post(
            "/api/documents",
            files={"file": ("p3.docx", _docx_bytes())},
            data={"title": "P v3", "doc_type": "policy", "supersedes_id": str(v1.id)},
            headers=headers,
        )
    assert response.status_code == 409
    assert response.json()["code"] == "document.previous_version_superseded"
    job.assert_not_awaited()
    assert await db_session.scalar(
        select(func.count()).select_from(Document).where(Document.title == "P v3")
    ) == 0


@pytest.mark.asyncio
async def test_plain_text_fallback_on_a_new_version_supersedes_the_previous_one(
    client, db_session
):
    from app.ingest.models import DocStatus, DocType, Document

    await _seed(db_session, Role.CONTRIBUTOR, "pv@example.com")
    headers = await _auth(client, "pv@example.com")
    previous = Document(
        title="P v1", doc_type=DocType.POLICY, file_hash="e" * 64,
        file_path="/v1.pdf", original_filename="v1.pdf", status=DocStatus.ACTIVE,
    )
    db_session.add(previous)
    await db_session.flush()
    new = Document(
        title="P v2", doc_type=DocType.POLICY, file_hash="f" * 64,
        file_path="/v2.pdf", original_filename="v2.pdf", status=DocStatus.PARSE_FAILED,
        supersedes_id=previous.id,
    )
    db_session.add(new)
    await db_session.flush()

    with patch("app.ingest.router.enqueue", new=AsyncMock()):
        response = await client.post(
            f"/api/documents/{new.id}/plain-text",
            json={"text": "1 Scope\nAccess shall be reviewed."},
            headers=headers,
        )
    assert response.status_code == 200
    await db_session.refresh(previous)
    assert previous.status is DocStatus.SUPERSEDED


@pytest.mark.asyncio
async def test_reparse_refuses_a_superseded_document(client, db_session):
    # 解析成功会把状态写成 active——重新解析一份已被替换的旧版，等于悄悄把它
    # 复活成现行版本，和新版并存。
    from app.ingest.models import DocStatus, DocType, Document

    await _seed(db_session, Role.CONTRIBUTOR, "old@example.com")
    headers = await _auth(client, "old@example.com")
    doc = Document(
        title="Old", doc_type=DocType.POLICY, file_hash="1" * 64,
        file_path="/old.pdf", original_filename="old.pdf", status=DocStatus.SUPERSEDED,
    )
    db_session.add(doc)
    await db_session.flush()

    with patch("app.ingest.router.enqueue", new=AsyncMock()) as job:
        response = await client.post(f"/api/documents/{doc.id}/reparse", headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == "document.superseded"
    job.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_version_that_already_has_a_successor_takes_no_second_one(client, db_session):
    # 同一份现行版被接了两个新版：两个都解析成功时，第二个去撤旧版发现已撤，跳过——
    # 两个新版同时是现行版本。新版本只能接在「现行、且还没有后继」的那一版后面。
    from sqlalchemy import func, select

    from app.ingest.models import DocStatus, DocType, Document

    await _seed(db_session, Role.CONTRIBUTOR, "succ@example.com")
    headers = await _auth(client, "succ@example.com")
    current = Document(title="P v1", doc_type=DocType.POLICY, file_hash="1" * 64, file_path="/1",
                       original_filename="1.pdf", status=DocStatus.ACTIVE)
    db_session.add(current)
    await db_session.flush()
    pending = Document(title="P v2", doc_type=DocType.POLICY, file_hash="2" * 64, file_path="/2",
                       original_filename="2.pdf", status=DocStatus.PARSE_FAILED, supersedes_id=current.id)
    db_session.add(pending)
    await db_session.flush()

    with patch("app.ingest.router.enqueue", new=AsyncMock()) as job:
        response = await client.post(
            "/api/documents", headers=headers,
            files={"file": ("p3.docx", _docx_bytes())},
            data={"title": "P v3", "doc_type": "policy", "supersedes_id": str(current.id)},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "document.previous_version_has_successor"
    assert response.json()["params"] == {"id": pending.id, "title": "P v2"}
    job.assert_not_awaited()
    assert await db_session.scalar(
        select(func.count()).select_from(Document).where(Document.supersedes_id == current.id)
    ) == 1


@pytest.mark.asyncio
async def test_a_new_version_attaches_only_to_an_active_document(client, db_session):
    # 接在一份从没生效过（解析失败）的文档后面：新版成功时撤掉的是它，真正的现行版
    # 没人撤，两版并存。
    from app.ingest.models import DocStatus, DocType, Document

    await _seed(db_session, Role.CONTRIBUTOR, "inact@example.com")
    headers = await _auth(client, "inact@example.com")
    failed = Document(title="P draft", doc_type=DocType.POLICY, file_hash="3" * 64, file_path="/3",
                      original_filename="3.pdf", status=DocStatus.PARSE_FAILED)
    db_session.add(failed)
    await db_session.flush()

    with patch("app.ingest.router.enqueue", new=AsyncMock()) as job:
        response = await client.post(
            "/api/documents", headers=headers,
            files={"file": ("p4.docx", _docx_bytes())},
            data={"title": "P v4", "doc_type": "policy", "supersedes_id": str(failed.id)},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "document.previous_version_not_active"
    job.assert_not_awaited()
