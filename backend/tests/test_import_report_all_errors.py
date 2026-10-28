"""Bug 复现（探索）测试：导入失败必须带上**全部**校验问题。

本文件编码的是**修好之后**的期望行为——导入失败时抛一个「信封」错误，
`code` 为 `framework.import_invalid` / `matrix.import_invalid`，
`params = {"issues": [asdict(i) for i in report], "count": len(report)}`，
每条问题的 `code`/`params`（行号在 params 里）按报告顺序原样保留。

在**未修复**的代码上，这些断言必然失败：
- 框架侧 `import_framework` 只抛 `report[0]`（某个 `framework.row_*` code），
  没有 `params["issues"]`；
- 矩阵侧三个校验点都 `raise report[0].rejected()`（某个 `matrix.*` code），
  同样没有 `params["issues"]`。

失败即确认 bug 存在（`report[1:]` 被丢弃）。此步**不修复代码**。
"""

import hashlib
import io
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from app.frameworks.importer import FrameworkImportError, import_framework
from app.frameworks.models import Framework, FrameworkItem
from app.frameworks.template import parse_rows
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.matrix.importer import import_confirmed, import_rows
from app.matrix.template import SheetRejected, read_sheet, validate
from app.review.models import Proposal, ProposalKind, ProposalStatus

# ---------------------------------------------------------------------------
# 构造「不止一条问题」的输入（isBugCondition(report) = len(report) > 1）
# ---------------------------------------------------------------------------

FRAMEWORK_HEADERS = ["code", "title", "description", "parent_code", "attributes_json"]

# 第 2 行 code 为空 → framework.row_code_title_required（row=2）
# 第 3 行 code 与第 1 行重复 → framework.row_code_duplicate（row=3）
FRAMEWORK_MULTI_ISSUE_ROWS = [
    ["A", "第一条", "", "", ""],
    ["", "缺少编号", "", "", ""],
    ["A", "编号重复", "", "", ""],
]

MATRIX_HEADERS = ["编号", "名称", "描述"]
MATRIX_MAPPING = {"code": "编号", "title": "名称", "statement": "描述"}

# 第 1 行 title 超长 → matrix.field_too_long（title，limit=500）
# 第 2 行 statement 为空 → matrix.required_field_blank（statement，count=1）
# 两条互不相同的 code，且都通过了结构校验（mapping/表头/列都合法）。
MATRIX_MULTI_ISSUE_ROWS = [
    ["C-1", "标" * 501, "有内容"],
    ["C-2", "正常标题", ""],
]


def _matrix_workbook(headers=None, rows=None) -> bytes:
    """落盘一个最小 XLSX，供 read_sheet 读回。"""
    book = Workbook()
    sheet = book.active
    sheet.append(headers if headers is not None else MATRIX_HEADERS)
    for row in rows if rows is not None else MATRIX_MULTI_ISSUE_ROWS:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def _mapping_proposal(content: bytes) -> Proposal:
    """构造一个与上传文件绑定一致的已确认映射提案。"""
    sheet = read_sheet(content)
    return Proposal(
        id=1,
        kind=ProposalKind.MATRIX_MAPPING,
        status=ProposalStatus.ACCEPTED,
        payload={
            "mapping": MATRIX_MAPPING,
            "source_sha256": hashlib.sha256(content).hexdigest(),
            "source_headers": sheet.headers,
            "source_rows": len(sheet.rows),
        },
    )


def _session_mock():
    """记录 add 的假 session；校验失败时应当一次 add 都不发生。"""
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
# 前置自检：确认构造出来的输入确实产生 ≥2 条问题，且 code 符合预期
# ---------------------------------------------------------------------------

def test_构造的框架输入确实产生两条问题():
    """探索测试的前提：parse_rows 要返回不止一条问题。"""
    _, report = parse_rows(FRAMEWORK_HEADERS, FRAMEWORK_MULTI_ISSUE_ROWS)
    codes = [issue.code for issue in report]
    assert len(report) >= 2, f"期望 ≥2 条问题，实际 {codes}"
    assert "framework.row_code_title_required" in codes
    assert "framework.row_code_duplicate" in codes


def test_构造的矩阵输入确实产生两条问题():
    """探索测试的前提：validate 要返回不止一条问题。"""
    sheet = read_sheet(_matrix_workbook())
    report = validate(sheet, MATRIX_MAPPING)
    codes = [issue.code for issue in report]
    assert len(report) >= 2, f"期望 ≥2 条问题，实际 {codes}"
    assert "matrix.required_field_blank" in codes
    assert "matrix.field_too_long" in codes


# ---------------------------------------------------------------------------
# 用例 1：框架多问题导入
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_框架多问题导入抛信封错误并带上全部问题(db_session):
    """import_framework 的 report 有 ≥2 条问题时，应抛 framework.import_invalid 信封。

    未修复代码只抛 report[0] 的 per-issue code，断言会失败——确认 bug。
    """
    actor = await _actor(db_session)
    _, report = parse_rows(FRAMEWORK_HEADERS, FRAMEWORK_MULTI_ISSUE_ROWS)
    assert len(report) >= 2  # 前提

    with pytest.raises(FrameworkImportError) as caught:
        await import_framework(
            db_session, FRAMEWORK_HEADERS, FRAMEWORK_MULTI_ISSUE_ROWS,
            key="multi-issue", name_zh="多问题", name_en="Multi", version="1.0",
            source="test", actor=actor,
        )

    error = caught.value
    # 修复后：信封 code + 完整问题列表（顺序、code、params 一致，行号在 params 里）
    assert error.code == "framework.import_invalid"
    assert error.params is not None
    assert error.params["count"] == len(report)
    assert error.params["issues"] == [asdict(issue) for issue in report]

    # 事务性：校验失败一行都不写。
    assert await db_session.scalar(select(func.count()).select_from(Framework)) == 0
    assert await db_session.scalar(select(func.count()).select_from(FrameworkItem)) == 0


# ---------------------------------------------------------------------------
# 用例 2：矩阵多问题导入（import_rows）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_矩阵import_rows多问题抛信封错误并带上全部问题():
    """import_rows 的 validate 报告有 ≥2 条问题时，应抛 matrix.import_invalid 信封。"""
    sheet = read_sheet(_matrix_workbook())
    report = validate(sheet, MATRIX_MAPPING)
    assert len(report) >= 2  # 前提

    session = _session_mock()
    with pytest.raises(SheetRejected) as caught:
        await import_rows(
            session, sheet, MATRIX_MAPPING, actor_id=5,
            mapping_proposal_id=1, source_sha256="a" * 64,
        )

    error = caught.value
    assert error.code == "matrix.import_invalid"
    assert error.params["count"] == len(report)
    assert error.params["issues"] == [asdict(issue) for issue in report]

    # 事务性：校验失败一条提案都不写。
    session.add.assert_not_called()


# ---------------------------------------------------------------------------
# 用例 3：矩阵确认路径多问题（import_confirmed）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_矩阵import_confirmed多问题抛信封错误并带上全部问题():
    """import_confirmed 对真实 sheet 跑 validate，报告 ≥2 条时应抛信封。"""
    content = _matrix_workbook()
    sheet = read_sheet(content)
    report = validate(sheet, MATRIX_MAPPING)
    assert len(report) >= 2  # 前提

    session = _session_mock()
    session.scalar.return_value = _mapping_proposal(content)

    with pytest.raises(SheetRejected) as caught:
        await import_confirmed(
            session, sheet, content, proposal_id=1, actor=SimpleNamespace(id=5),
        )

    error = caught.value
    assert error.code == "matrix.import_invalid"
    assert error.params["count"] == len(report)
    assert error.params["issues"] == [asdict(issue) for issue in report]

    # 事务性：校验失败一条提案都不写。
    session.add.assert_not_called()


# ---------------------------------------------------------------------------
# 用例 4：边界——恰好一条问题，也要用信封包住（count=1，不丢信息）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_框架恰好一条问题仍包进信封(db_session):
    """单条问题报告：修复后应是 count=1 的单元素信封。

    未修复代码直接抛那条 per-issue code（framework.row_code_duplicate），
    没有信封，断言同样失败。
    """
    actor = await _actor(db_session)
    rows = [
        ["A", "第一条", "", "", ""],
        ["A", "编号重复", "", "", ""],  # 唯一问题：code 重复
    ]
    _, report = parse_rows(FRAMEWORK_HEADERS, rows)
    assert len(report) == 1  # 前提：恰好一条
    assert report[0].code == "framework.row_code_duplicate"

    with pytest.raises(FrameworkImportError) as caught:
        await import_framework(
            db_session, FRAMEWORK_HEADERS, rows,
            key="single-issue", name_zh="单问题", name_en="Single", version="1.0",
            source="test", actor=actor,
        )

    error = caught.value
    assert error.code == "framework.import_invalid"
    assert error.params["count"] == 1
    assert error.params["issues"] == [asdict(report[0])]

    assert await db_session.scalar(select(func.count()).select_from(Framework)) == 0
    assert await db_session.scalar(select(func.count()).select_from(FrameworkItem)) == 0


@pytest.mark.asyncio
async def test_矩阵恰好一条问题仍包进信封():
    """矩阵单条问题（matrix.duplicate_codes）：修复后应是 count=1 的信封。"""
    content = _matrix_workbook(rows=[["AC-1", "标题甲", "描述甲"], ["AC-1", "标题乙", "描述乙"]])
    sheet = read_sheet(content)
    report = validate(sheet, MATRIX_MAPPING)
    assert len(report) == 1  # 前提：恰好一条
    assert report[0].code == "matrix.duplicate_codes"

    session = _session_mock()
    with pytest.raises(SheetRejected) as caught:
        await import_rows(
            session, sheet, MATRIX_MAPPING, actor_id=5,
            mapping_proposal_id=1, source_sha256="a" * 64,
        )

    error = caught.value
    assert error.code == "matrix.import_invalid"
    assert error.params["count"] == 1
    assert error.params["issues"] == [asdict(report[0])]
    session.add.assert_not_called()
