import io
import zipfile

from app.clauses.models import Clause
from app.controls.models import Control, ControlSource, SourceRelation
from app.frameworks.models import Framework, FrameworkItem
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.ingest.models import DocType, Document

# 夹具照抄 tests/test_graph.py；_framework 只返回 Framework，好让 framework.id 能用。
# _two_documents_in_conflict 照抄计划的共享测试夹具。


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


async def _framework(db_session, key: str = "nist-csf-2.0") -> Framework:
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
    return framework


async def test_the_package_holds_the_three_expected_members(client, db_session):
    framework = await _framework(db_session)
    await _user(db_session, email="v@example.com")
    headers = await _auth(client, "v@example.com")

    resp = await client.get(
        f"/api/frameworks/{framework.id}/readiness-package", headers=headers)

    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as bundle:
        assert sorted(bundle.namelist()) == [
            "evidence.xlsx", "mappings.xlsx", "readiness.docx"]


async def test_every_member_is_non_empty(client, db_session):
    framework = await _framework(db_session)
    await _user(db_session, email="v@example.com")
    headers = await _auth(client, "v@example.com")

    resp = await client.get(
        f"/api/frameworks/{framework.id}/readiness-package", headers=headers)

    with zipfile.ZipFile(io.BytesIO(resp.content)) as bundle:
        for name in bundle.namelist():
            assert len(bundle.read(name)) > 0


async def test_only_confirmed_conflicts_reach_the_package(client, db_session):
    # 准备包是拿去给审计看的，里面每一条都必须是人已经点过头的。
    from app.conflicts.models import PolicyConflict
    from app.review.models import Proposal, ProposalKind, ProposalStatus

    await _framework(db_session)
    a, b, _control_a, _control_b = await _two_documents_in_conflict(db_session)
    db_session.add(PolicyConflict(
        clause_a_id=a.id, clause_b_id=b.id, topic="已确认的冲突", difference="x"))
    db_session.add(Proposal(
        kind=ProposalKind.CONFLICT, status=ProposalStatus.PENDING, citations=[],
        payload={"clause_a_id": a.id, "clause_b_id": b.id, "topic": "待确认的冲突",
                 "difference": "y", "quote_a": "q", "quote_b": "q", "confidence": 0.5}))
    await db_session.flush()

    from app.packaging.service import conflict_rows

    rows = await conflict_rows(db_session)

    assert [row.topic for row in rows] == ["已确认的冲突"]


async def test_an_unknown_framework_is_a_404(client, db_session):
    await _user(db_session, email="v@example.com")
    headers = await _auth(client, "v@example.com")

    resp = await client.get("/api/frameworks/999999/readiness-package", headers=headers)

    assert resp.status_code == 404


async def test_an_anonymous_request_is_a_401(client, db_session):
    framework = await _framework(db_session)

    resp = await client.get(f"/api/frameworks/{framework.id}/readiness-package")

    assert resp.status_code == 401


async def test_the_package_is_in_english_by_default(client, db_session):
    # 产品只有英文界面。原来接口默认 language="zh"、前端又不传，导出给审计的准备包
    # 是中文的；两张 Excel 的表头更是写死中文，不管 language 传什么。
    from docx import Document as WordDocument
    from openpyxl import load_workbook

    framework = await _framework(db_session)
    await _user(db_session, email="v@example.com")
    headers = await _auth(client, "v@example.com")

    resp = await client.get(f"/api/frameworks/{framework.id}/readiness-package", headers=headers)

    with zipfile.ZipFile(io.BytesIO(resp.content)) as bundle:
        for name in ("mappings.xlsx", "evidence.xlsx"):
            sheet = load_workbook(io.BytesIO(bundle.read(name))).active
            row = [cell.value for cell in next(sheet.iter_rows(max_row=1))]
            assert row[0] == "Control code", name
        text = "\n".join(p.text for p in WordDocument(io.BytesIO(bundle.read("readiness.docx"))).paragraphs)
        assert "Audit Readiness Package" in text
        assert "审计准备包" not in text
