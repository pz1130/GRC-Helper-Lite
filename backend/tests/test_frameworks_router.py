import io

import pytest
from openpyxl import Workbook

from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password


async def _seed(db_session, role: Role, email: str) -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str) -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _workbook(rows) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(["code", "title", "description", "parent_code", "attributes_json"])
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


GOOD = [["PR", "Protect", "", "", ""], ["PR.AA-01", "Identities", "Body.", "PR", ""]]
BAD = [["PR.AA-01", "Identities", "Body.", "MISSING", ""]]


@pytest.mark.asyncio
async def test_validate_reports_problems_without_writing(client, db_session):
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")
    resp = await client.post(
        "/api/frameworks/validate",
        headers=headers,
        files={"file": ("f.xlsx", _workbook(BAD),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    # 报告是 code + 参数（前端按语言组句），不是中文句子。
    assert [(line["code"], line["params"]) for line in body["report"]] == [
        ("framework.parent_missing", {"code": "PR.AA-01", "parent": "MISSING"}),
    ]

    listing = await client.get("/api/frameworks", headers=headers)
    assert listing.json() == []


@pytest.mark.asyncio
async def test_import_then_browse_tree_coverage_and_gaps(client, db_session):
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")
    resp = await client.post(
        "/api/frameworks/import",
        headers=headers,
        data={"key": "csf", "name_zh": "CSF", "name_en": "CSF",
              "version": "2.0", "source": "nist.gov"},
        files={"file": ("f.xlsx", _workbook(GOOD),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    framework_id = resp.json()["id"]

    tree = await client.get(f"/api/frameworks/{framework_id}/tree", headers=headers)
    assert [item["code"] for item in tree.json()] == ["PR", "PR.AA-01"]

    coverage = await client.get(f"/api/frameworks/{framework_id}/coverage", headers=headers)
    root = next(row for row in coverage.json() if row["code"] == "PR")
    assert root["requirements"] == 1 and root["covered"] == 0

    gaps = await client.get(f"/api/frameworks/{framework_id}/gaps", headers=headers)
    assert [gap["code"] for gap in gaps.json()] == ["PR.AA-01"]


@pytest.mark.asyncio
async def test_contributor_can_read_but_not_import(client, db_session):
    await _seed(db_session, Role.CONTRIBUTOR, "contrib@example.com")
    headers = await _auth(client, "contrib@example.com")
    assert (await client.get("/api/frameworks", headers=headers)).status_code == 200
    resp = await client.post(
        "/api/frameworks/import",
        headers=headers,
        data={"key": "csf", "name_zh": "CSF", "name_en": "CSF",
              "version": "2.0", "source": "nist.gov"},
        files={"file": ("f.xlsx", _workbook(GOOD),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_contributor_cannot_trigger_mapping(client, db_session):
    await _seed(db_session, Role.CONTRIBUTOR, "contrib@example.com")
    headers = await _auth(client, "contrib@example.com")
    resp = await client.post("/api/mapping/frameworks/1", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_mapping_an_unknown_framework_is_404(client, db_session):
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")
    resp = await client.post("/api/mapping/frameworks/999999", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_import_rejections_carry_a_code(client, db_session):
    # 原来是 FrameworkImportError 的类级 code「framework_import_error」加一句中文，
    # 词条表里没有它，i18n-check 也扫不到类级 code，英文界面显示中文。
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")
    form = {"key": "csf", "name_zh": "CSF", "name_en": "CSF", "version": "2.0", "source": "nist.gov"}
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    bad = await client.post("/api/frameworks/import", headers=headers, data=form,
                            files={"file": ("f.xlsx", _workbook(BAD), xlsx)})
    assert bad.status_code == 400
    assert (bad.json()["code"], bad.json()["params"]) == (
        "framework.parent_missing", {"code": "PR.AA-01", "parent": "MISSING"},
    )

    first = await client.post("/api/frameworks/import", headers=headers, data=form,
                              files={"file": ("f.xlsx", _workbook(GOOD), xlsx)})
    assert first.status_code == 200
    again = await client.post("/api/frameworks/import", headers=headers, data=form,
                              files={"file": ("f.xlsx", _workbook(GOOD), xlsx)})
    assert (again.json()["code"], again.json()["params"]) == ("framework.key_exists", {"key": "csf"})

    unreadable = await client.post("/api/frameworks/validate", headers=headers,
                                   files={"file": ("f.xlsx", b"not excel", xlsx)})
    assert unreadable.json()["code"] == "matrix.unreadable"


@pytest.mark.asyncio
async def test_a_framework_imports_without_a_chinese_name(client, db_session):
    # 产品只有英文界面（2026-09-23 起），导入表单上已经没有中文名这一栏。
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")
    resp = await client.post(
        "/api/frameworks/import", headers=headers,
        data={"key": "csf", "name_en": "CSF", "version": "2.0", "source": "nist.gov"},
        files={"file": ("f.xlsx", _workbook(GOOD),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    assert resp.json()["name_zh"] == ""
