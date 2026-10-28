"""Task 2 保留测试（观察优先）：非多问题输入在修复前后行为不变。

本文件只断言**未修复代码**上已经成立的行为（NOT isBugCondition 的分支，
需求 3.1-3.6）：先观察真实行为，再把它锁死。这些测试必须在未修复代码上**通过**。

覆盖：
- 空报告导入成功（framework）：合法文件 → 写入 Framework，item_count 正确。
- 空报告导入成功（matrix）：合法 sheet 过 import_confirmed → 建提案。
- framework.key_exists 不变：重复 key 抛单条 framework.key_exists（非信封）。
- 矩阵单条件错误不变：file_mismatch / mapping_mismatch / proposal_not_found /
  source_binding_changed 各自抛自己的单 code。
- /validate 形状不变：framework 与 matrix 的 /validate 仍返回
  {"ok", "report": [{code, detail, params}, ...], ...}。
- 失败时一行都不写：校验失败的导入不写任何行（事务性不变量）。
"""

import hashlib
import io
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app.errors import Conflict, NotFound
from app.frameworks.importer import FrameworkImportError, import_framework
from app.frameworks.models import Framework, FrameworkItem
from app.frameworks.template import parse_rows
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.matrix.importer import (
    import_confirmed,
    import_rows,
    validate_mapping_proposal,
)
from app.matrix.template import SheetRejected, read_sheet, validate
from app.review.models import Proposal, ProposalKind, ProposalStatus

# ---------------------------------------------------------------------------
# 复用 Task 1 的常量与构造风格（见 test_import_report_all_errors.py）
# ---------------------------------------------------------------------------

FRAMEWORK_HEADERS = ["code", "title", "description", "parent_code", "attributes_json"]

# 合法框架：一棵 PR → PR.AA-01 的小树，报告为空 → 导入成功。
FRAMEWORK_GOOD_ROWS = [
    ["PR", "Protect", "", "", ""],
    ["PR.AA-01", "Identities", "Body.", "PR", ""],
]

MATRIX_HEADERS = ["编号", "名称", "描述"]
MATRIX_MAPPING = {"code": "编号", "title": "名称", "statement": "描述"}

# 合法矩阵：报告为空 → import_confirmed 建提案。
MATRIX_GOOD_ROWS = [
    ["C-1", "控制一", "描述一"],
    ["C-2", "控制二", "描述二"],
]


def _matrix_workbook(headers=None, rows=None) -> bytes:
    """落盘一个最小 XLSX，供 read_sheet 读回（镜像 Task 1 的 helper）。"""
    book = Workbook()
    sheet = book.active
    sheet.append(headers if headers is not None else MATRIX_HEADERS)
    for row in rows if rows is not None else MATRIX_GOOD_ROWS:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def _mapping_proposal(content: bytes, **kwargs) -> Proposal:
    """构造一个与上传文件绑定一致的映射提案（镜像 Task 1 的 helper）。"""
    sheet = read_sheet(content)
    return Proposal(
        id=1,
        kind=ProposalKind.MATRIX_MAPPING,
        status=kwargs.pop("status", ProposalStatus.ACCEPTED),
        payload={
            "mapping": MATRIX_MAPPING,
            "source_sha256": hashlib.sha256(content).hexdigest(),
            "source_headers": sheet.headers,
            "source_rows": len(sheet.rows),
        },
        **kwargs,
    )


def _session_mock():
    """记录 add 的假 session（镜像 Task 1 的 helper）。"""
    from unittest.mock import AsyncMock, Mock

    session = Mock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.scalar = AsyncMock()
    added = []

    def add(value):
        value.id = len(added) + 10
        added.append(value)

    session.add.side_effect = add
    session.added = added
    return session


async def _actor(db_session) -> User:
    user = User(
        email="lead@example.com", name="Lead", role=Role.GRC_LEAD,
        password_hash=hash_password("pw123456"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


# ---------------------------------------------------------------------------
# HTTP 测试客户端：授权一个有写权限的 Writer（镜像 test_frameworks_router.py）
# ---------------------------------------------------------------------------

async def _seed(db_session, role: Role, email: str) -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str) -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------------------------
# 前置自检：确认「好」输入的报告确实为空（NOT isBugCondition）
# ---------------------------------------------------------------------------

def test_合法框架输入报告为空():
    """保留测试前提：合法框架文件 parse_rows 报告为空。"""
    _, report = parse_rows(FRAMEWORK_HEADERS, FRAMEWORK_GOOD_ROWS)
    assert report == []


def test_合法矩阵输入报告为空():
    """保留测试前提：合法 sheet validate 报告为空。"""
    sheet = read_sheet(_matrix_workbook())
    assert validate(sheet, MATRIX_MAPPING) == []


# ---------------------------------------------------------------------------
# 空报告导入成功（framework）：写入 Framework 行，item_count 正确
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_空报告框架导入成功落库且计数正确(db_session):
    """report 为空 → import_framework 写入 Framework，item_count == 行数。"""
    actor = await _actor(db_session)
    framework = await import_framework(
        db_session, FRAMEWORK_HEADERS, FRAMEWORK_GOOD_ROWS,
        key="csf", name_zh="CSF", name_en="CSF", version="2.0",
        source="nist.gov", actor=actor,
    )

    assert framework.item_count == len(FRAMEWORK_GOOD_ROWS)
    assert await db_session.scalar(select(func.count()).select_from(Framework)) == 1
    assert await db_session.scalar(
        select(func.count()).select_from(FrameworkItem)
    ) == len(FRAMEWORK_GOOD_ROWS)
    codes = list(await db_session.scalars(
        select(FrameworkItem.code).order_by(FrameworkItem.order_index)
    ))
    assert codes == ["PR", "PR.AA-01"]


# ---------------------------------------------------------------------------
# 空报告导入成功（matrix）：import_confirmed 建提案
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_空报告矩阵导入成功建提案():
    """合法 sheet 过 import_confirmed → 为每行建一个 CONTROL_EXTRACT 提案 + 审计。"""
    content = _matrix_workbook()
    sheet = read_sheet(content)
    assert validate(sheet, MATRIX_MAPPING) == []  # 前提

    session = _session_mock()
    session.scalar.side_effect = [_mapping_proposal(content), None]

    result = await import_confirmed(
        session, sheet, content, proposal_id=1, actor=SimpleNamespace(id=5),
    )

    assert result["imported"] == len(MATRIX_GOOD_ROWS)
    proposals = [a for a in session.added if isinstance(a, Proposal)]
    assert len(proposals) == len(MATRIX_GOOD_ROWS)
    assert all(p.kind == ProposalKind.CONTROL_EXTRACT for p in proposals)
    assert all(p.status == ProposalStatus.PENDING for p in proposals)
    assert [p.payload["code"] for p in proposals] == ["C-1", "C-2"]


# ---------------------------------------------------------------------------
# framework.key_exists 不变：重复 key 抛单条 framework.key_exists（非信封）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_框架重复key抛单条key_exists而非信封(db_session):
    """预先导入同一个 key，再次导入合法文件 → framework.key_exists，params={"key": ...}。"""
    actor = await _actor(db_session)
    await import_framework(
        db_session, FRAMEWORK_HEADERS, FRAMEWORK_GOOD_ROWS,
        key="csf", name_zh="CSF", name_en="CSF", version="2.0",
        source="nist.gov", actor=actor,
    )

    with pytest.raises(FrameworkImportError) as caught:
        await import_framework(
            db_session, FRAMEWORK_HEADERS, FRAMEWORK_GOOD_ROWS,
            key="csf", name_zh="CSF", name_en="CSF", version="2.1",
            source="nist.gov", actor=actor,
        )

    error = caught.value
    assert error.code == "framework.key_exists"
    assert error.params == {"key": "csf"}
    # 不是信封：没有 issues/count。
    assert "issues" not in error.params
    assert "count" not in error.params

    # 第二次导入一行都没多写：仍然只有第一次的那个 framework。
    assert await db_session.scalar(select(func.count()).select_from(Framework)) == 1


# ---------------------------------------------------------------------------
# 矩阵单条件错误不变：各自抛自己的单 code（非信封）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_矩阵proposal_not_found单条码不变():
    """session.scalar 返回 None → NotFound(matrix.proposal_not_found)。"""
    content = _matrix_workbook()
    sheet = read_sheet(content)
    session = _session_mock()
    session.scalar.return_value = None

    with pytest.raises(NotFound) as caught:
        await import_confirmed(
            session, sheet, content, proposal_id=1, actor=SimpleNamespace(id=5),
        )

    error = caught.value
    assert error.code == "matrix.proposal_not_found"
    assert "issues" not in (error.params or {})
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_矩阵mapping_mismatch单条码不变():
    """传入与已确认映射不一致的 mapping → Conflict(matrix.mapping_mismatch)。"""
    content = _matrix_workbook()
    sheet = read_sheet(content)
    session = _session_mock()
    session.scalar.return_value = _mapping_proposal(content)

    with pytest.raises(Conflict) as caught:
        await import_confirmed(
            session, sheet, content, proposal_id=1, actor=SimpleNamespace(id=5),
            mapping={"code": "编号", "title": "描述", "statement": "名称"},
        )

    error = caught.value
    assert error.code == "matrix.mapping_mismatch"
    assert "issues" not in (error.params or {})
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_矩阵file_mismatch单条码不变():
    """上传文件的字节与提案绑定的原始文件不一致 → Conflict(matrix.file_mismatch)。"""
    content = _matrix_workbook()
    sheet = read_sheet(content)
    session = _session_mock()
    session.scalar.return_value = _mapping_proposal(content)

    with pytest.raises(Conflict) as caught:
        await import_confirmed(
            session, sheet, content + b"changed", proposal_id=1,
            actor=SimpleNamespace(id=5),
        )

    error = caught.value
    assert error.code == "matrix.file_mismatch"
    assert "issues" not in (error.params or {})
    session.add.assert_not_called()


def test_矩阵source_binding_changed单条码不变():
    """确认钩子里改动来源绑定字段 → SheetRejected(matrix.source_binding_changed)。"""
    content = _matrix_workbook()
    proposal = _mapping_proposal(content)
    changed_sha = "b" * 64

    with pytest.raises(SheetRejected) as caught:
        validate_mapping_proposal(
            proposal, {"mapping": MATRIX_MAPPING, "source_sha256": changed_sha},
        )

    error = caught.value
    assert error.code == "matrix.source_binding_changed"
    assert error.params == {"key": "source_sha256"}
    assert "issues" not in error.params


# ---------------------------------------------------------------------------
# 失败时一行都不写：校验失败的导入不写任何行（单问题路径即可验证）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_框架校验失败一行都不写(db_session):
    """单条校验失败（parent_missing）→ 抛错且 Framework/FrameworkItem 均为 0。"""
    actor = await _actor(db_session)
    bad = [["PR.AA-01", "Identities", "Body.", "MISSING", ""]]
    _, report = parse_rows(FRAMEWORK_HEADERS, bad)
    assert len(report) == 1  # 单问题路径
    assert report[0].code == "framework.parent_missing"

    with pytest.raises(FrameworkImportError):
        await import_framework(
            db_session, FRAMEWORK_HEADERS, bad,
            key="csf", name_zh="CSF", name_en="CSF", version="2.0",
            source="nist.gov", actor=actor,
        )

    assert await db_session.scalar(select(func.count()).select_from(Framework)) == 0
    assert await db_session.scalar(select(func.count()).select_from(FrameworkItem)) == 0


@pytest.mark.asyncio
async def test_矩阵校验失败一条提案都不写():
    """单条校验失败（required_field_blank）→ import_rows 抛错且一次 add 都没有。"""
    content = _matrix_workbook(rows=[["C-1", "标题", ""]])  # statement 为空
    sheet = read_sheet(content)
    report = validate(sheet, MATRIX_MAPPING)
    assert len(report) == 1  # 单问题路径
    assert report[0].code == "matrix.required_field_blank"

    session = _session_mock()
    with pytest.raises(SheetRejected):
        await import_rows(
            session, sheet, MATRIX_MAPPING, actor_id=5,
            mapping_proposal_id=1, source_sha256="a" * 64,
        )

    session.add.assert_not_called()


# ---------------------------------------------------------------------------
# /validate 形状不变（framework）：{"ok", "report": [{code, detail, params}], ...}
# ---------------------------------------------------------------------------

def _framework_router_workbook(rows) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(FRAMEWORK_HEADERS)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.mark.asyncio
async def test_framework_validate_形状不变(client, db_session):
    """POST /api/frameworks/validate 仍返回 {"ok", "report": [{code, detail, params}], ...}。"""
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")
    bad = [["PR.AA-01", "Identities", "Body.", "MISSING", ""]]

    resp = await client.post(
        "/api/frameworks/validate", headers=headers,
        files={"file": ("f.xlsx", _framework_router_workbook(bad), _XLSX)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert isinstance(body["report"], list) and body["report"]
    for line in body["report"]:
        assert set(line) == {"code", "detail", "params"}
    assert [(line["code"], line["params"]) for line in body["report"]] == [
        ("framework.parent_missing", {"code": "PR.AA-01", "parent": "MISSING"}),
    ]


@pytest.mark.asyncio
async def test_framework_validate_合法文件ok为真(client, db_session):
    """合法文件 → ok=True，report 为空列表，形状不变。"""
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")

    resp = await client.post(
        "/api/frameworks/validate", headers=headers,
        files={"file": ("f.xlsx", _framework_router_workbook(FRAMEWORK_GOOD_ROWS), _XLSX)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["report"] == []


# ---------------------------------------------------------------------------
# /validate 形状不变（matrix）：{"ok", "report": [{code, detail, params}], ...}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_matrix_validate_形状不变(client, db_session):
    """POST /api/matrix/validate 仍返回 {"ok", "report": [{code, detail, params}], ...}。"""
    import json

    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")
    # 映射到不存在的列 → 单条 matrix.column_missing。
    mapping = {"title": "名称", "statement": "不存在的列"}

    resp = await client.post(
        "/api/matrix/validate", headers=headers,
        files={"file": ("x.xlsx", _matrix_workbook(), _XLSX)},
        data={"mapping_json": json.dumps(mapping)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert isinstance(body["report"], list) and body["report"]
    for line in body["report"]:
        assert set(line) == {"code", "detail", "params"}
    assert [(line["code"], line["params"]) for line in body["report"]] == [
        ("matrix.column_missing", {"column": "不存在的列"}),
    ]


@pytest.mark.asyncio
async def test_matrix_validate_合法文件ok为真(client, db_session):
    """合法 sheet + 合法映射 → ok=True，report 为空，形状不变。"""
    import json

    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")

    resp = await client.post(
        "/api/matrix/validate", headers=headers,
        files={"file": ("x.xlsx", _matrix_workbook(), _XLSX)},
        data={"mapping_json": json.dumps(MATRIX_MAPPING)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["report"] == []
