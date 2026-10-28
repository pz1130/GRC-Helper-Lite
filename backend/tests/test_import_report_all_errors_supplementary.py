"""Task 4 补充测试：信封助手、序列化与 HTTP 集成。

本文件补齐 Task 1/2 没覆盖、又值得单独锁死的几块（需求 2.1-2.4、3.4-3.6）：

单元（纯函数，不碰数据库）：
- rejected_report(report)：给一份 list[Issue]，返回 code="matrix.import_invalid"、
  params={"issues": [asdict(i) ...], "count": N} 的 SheetRejected；每条等于 asdict(issue)。
- 恰好一条问题（count=1 边界）：单元素 issues，信息不丢。
- 序列化：信封错误（matrix 与 framework 两侧）过 AppError 处理器 / jsonable_encoder
  后得到干净的 {code, message, params: {issues:[...], count}}（嵌套 dict 列表）。
- framework 侧 count=1 边界：FrameworkImportError 单条问题仍包进信封。

集成（HTTP，复用 client fixture + Writer 鉴权，镜像 test_frameworks_router.py）：
- POST /api/frameworks/import 多问题文件 → 400 + {code:"framework.import_invalid",
  message, params:{issues,count}}，带上全部问题；库里一个 Framework 都没建。
- POST /api/matrix/import 多问题 sheet → {code:"matrix.import_invalid", ...}，
  带上全部问题；一条 Proposal 都没建。

/validate 形状回归已由 Task 2 覆盖，这里不重复。
"""

import hashlib
import io
import json
from dataclasses import asdict

import pytest
from fastapi.encoders import jsonable_encoder
from openpyxl import Workbook
from sqlalchemy import func, select

from app.errors import install_error_handlers
from app.frameworks.importer import FrameworkImportError
from app.frameworks.models import Framework, FrameworkItem
from app.frameworks.template import parse_rows
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.matrix.template import Issue, SheetRejected, read_sheet, rejected_report, validate
from app.review.models import Proposal, ProposalKind, ProposalStatus

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# ---------------------------------------------------------------------------
# 复用 Task 1 的多问题构造（见 test_import_report_all_errors.py）
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

# 第 1 行 title 超长 → matrix.field_too_long；第 2 行 statement 为空 →
# matrix.required_field_blank。两条互不相同，且结构校验（表头/映射/列）全过。
MATRIX_MULTI_ISSUE_ROWS = [
    ["C-1", "标" * 501, "有内容"],
    ["C-2", "正常标题", ""],
]


def _matrix_workbook(headers=None, rows=None) -> bytes:
    """落盘一个最小 XLSX（镜像 Task 1 的 helper）。"""
    book = Workbook()
    sheet = book.active
    sheet.append(headers if headers is not None else MATRIX_HEADERS)
    for row in rows if rows is not None else MATRIX_MULTI_ISSUE_ROWS:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def _framework_workbook(rows) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(FRAMEWORK_HEADERS)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def _seed(db_session, role: Role, email: str) -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str) -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ===========================================================================
# 单元：rejected_report 把整份报告打包成信封
# ===========================================================================

def test_rejected_report_打包成matrix信封():
    """给一份多问题报告 → code=matrix.import_invalid，issues 逐条等于 asdict(issue)。"""
    report = [
        Issue(code="matrix.required_field_blank", detail="statement 为空", params={"count": 1, "field": "statement"}),
        Issue(code="matrix.field_too_long", detail="title 超长", params={"field": "title", "limit": 500}),
    ]

    rejected = rejected_report(report)

    assert isinstance(rejected, SheetRejected)
    assert rejected.code == "matrix.import_invalid"
    assert rejected.params["count"] == len(report) == 2
    assert rejected.params["issues"] == [asdict(issue) for issue in report]
    # 顺序与每条 code/params 原样保留。
    assert [entry["code"] for entry in rejected.params["issues"]] == [
        "matrix.required_field_blank", "matrix.field_too_long",
    ]
    assert rejected.params["issues"][0] == asdict(report[0])
    assert rejected.params["issues"][1] == asdict(report[1])


def test_rejected_report_恰好一条问题仍包信封():
    """count=1 边界：单元素 issues，信息不丢（需求 3.5）。"""
    report = [Issue(code="matrix.duplicate_codes", detail="编号重复：AC-1", params={"codes": ["AC-1"]})]

    rejected = rejected_report(report)

    assert rejected.code == "matrix.import_invalid"
    assert rejected.params["count"] == 1
    assert rejected.params["issues"] == [asdict(report[0])]


def test_rejected_report_真实validate报告():
    """用真实 validate 产出的报告（而非手搓 Issue）过助手，端到端对齐。"""
    sheet = read_sheet(_matrix_workbook())
    report = validate(sheet, MATRIX_MAPPING)
    assert len(report) >= 2  # 前提

    rejected = rejected_report(report)

    assert rejected.code == "matrix.import_invalid"
    assert rejected.params["count"] == len(report)
    assert rejected.params["issues"] == [asdict(issue) for issue in report]


# ===========================================================================
# 单元：信封过 jsonable_encoder / AppError 处理器后序列化干净
# ===========================================================================

def test_matrix信封可被jsonable_encoder干净序列化():
    """嵌套 dict 列表：issues 里每条是 {code, detail, params}，params 还是 dict。"""
    sheet = read_sheet(_matrix_workbook())
    report = validate(sheet, MATRIX_MAPPING)
    rejected = rejected_report(report)

    encoded = jsonable_encoder(rejected.params)

    assert encoded["count"] == len(report)
    assert isinstance(encoded["issues"], list)
    for entry in encoded["issues"]:
        assert set(entry) == {"code", "detail", "params"}
        assert isinstance(entry["params"], dict)
    # 过一遍 json 往返也不炸（纯粹是 JSON 可表达的结构）。
    assert json.loads(json.dumps(encoded)) == encoded


def test_framework信封可被jsonable_encoder干净序列化():
    """框架侧信封（FrameworkImportError.params）同样是嵌套 dict 列表。"""
    _, report = parse_rows(FRAMEWORK_HEADERS, FRAMEWORK_MULTI_ISSUE_ROWS)
    error = FrameworkImportError(
        f"框架导入校验未通过，共 {len(report)} 个问题",
        code="framework.import_invalid",
        params={"issues": [asdict(issue) for issue in report], "count": len(report)},
    )

    encoded = jsonable_encoder(error.params)

    assert encoded["count"] == len(report)
    for entry in encoded["issues"]:
        assert set(entry) == {"code", "detail", "params"}
    assert json.loads(json.dumps(encoded)) == encoded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "error_factory"),
    [
        (
            "matrix.import_invalid",
            lambda: rejected_report(validate(read_sheet(_matrix_workbook()), MATRIX_MAPPING)),
        ),
        (
            "framework.import_invalid",
            lambda: FrameworkImportError(
                "框架导入校验未通过",
                code="framework.import_invalid",
                params={
                    "issues": [asdict(i) for i in parse_rows(FRAMEWORK_HEADERS, FRAMEWORK_MULTI_ISSUE_ROWS)[1]],
                    "count": len(parse_rows(FRAMEWORK_HEADERS, FRAMEWORK_MULTI_ISSUE_ROWS)[1]),
                },
            ),
        ),
    ],
)
async def test_信封经AppError处理器序列化成规范响应体(code, error_factory):
    """装上处理器后抛信封 → 响应体是 {code, message, params:{issues:[...], count}}。

    SheetRejected 是 ValueError，不是 AppError，处理器只接 AppError；因此矩阵侧
    用路由里同样的转换（_rejected 把 SheetRejected 的 code/params 搬进 AppError）。
    """
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.errors import AppError
    from app.matrix.router import _rejected

    error = error_factory()
    # 统一成 AppError：矩阵侧走路由的 _rejected，框架侧本身就是 AppError。
    app_error = _rejected(error) if isinstance(error, ValueError) and not isinstance(error, AppError) else error

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def _boom() -> dict[str, str]:  # pragma: no cover - 仅为触发处理器
        raise app_error

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        resp = await http.get("/boom")

    body = resp.json()
    assert body["code"] == code
    assert isinstance(body["message"], str) and body["message"]
    assert set(body["params"]) == {"issues", "count"}
    assert body["params"]["count"] == len(body["params"]["issues"])
    for entry in body["params"]["issues"]:
        assert set(entry) == {"code", "detail", "params"}


# ===========================================================================
# 集成（HTTP）：框架多问题导入 → 400 信封，库里一行都没写
# ===========================================================================

@pytest.mark.asyncio
async def test_框架import多问题返回信封且库不变(client, db_session):
    """POST /api/frameworks/import 多问题文件 → 400 framework.import_invalid 信封。"""
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")

    # 期望的信封内容：直接用 parse_rows 跑出同一份报告来对齐。
    _, report = parse_rows(FRAMEWORK_HEADERS, FRAMEWORK_MULTI_ISSUE_ROWS)
    assert len(report) >= 2  # 前提：确实多问题

    resp = await client.post(
        "/api/frameworks/import", headers=headers,
        data={"key": "csf", "name_zh": "CSF", "name_en": "CSF", "version": "2.0", "source": "nist.gov"},
        files={"file": ("f.xlsx", _framework_workbook(FRAMEWORK_MULTI_ISSUE_ROWS), _XLSX)},
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "framework.import_invalid"
    assert isinstance(body["message"], str) and body["message"]
    assert body["params"]["count"] == len(report)
    assert body["params"]["issues"] == [asdict(issue) for issue in report]
    # 每条问题的 code/params（行号在 params 里）原样保留，顺序一致。
    assert [entry["code"] for entry in body["params"]["issues"]] == [issue.code for issue in report]

    # 事务性：校验失败一个 Framework / FrameworkItem 都没建。
    assert await db_session.scalar(select(func.count()).select_from(Framework)) == 0
    assert await db_session.scalar(select(func.count()).select_from(FrameworkItem)) == 0
    assert (await client.get("/api/frameworks", headers=headers)).json() == []


# ===========================================================================
# 集成（HTTP）：矩阵多问题导入 → 信封，一条提案都没建
# ===========================================================================

@pytest.mark.asyncio
async def test_矩阵import多问题返回信封且不建提案(client, db_session):
    """POST /api/matrix/import 多问题 sheet → matrix.import_invalid 信封，无 Proposal 落库。

    /import 需要一个已确认（ACCEPTED）的 MATRIX_MAPPING 提案，且与上传文件字节绑定
    一致；这里直接在库里种一个，再上传同一份多问题文件走到 validate 失败。
    """
    actor = await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")

    content = _matrix_workbook()
    sheet = read_sheet(content)
    report = validate(sheet, MATRIX_MAPPING)
    assert len(report) >= 2  # 前提：确实多问题

    # 种一个与文件绑定一致的已确认映射提案。
    mapping_proposal = Proposal(
        kind=ProposalKind.MATRIX_MAPPING,
        status=ProposalStatus.ACCEPTED,
        payload={
            "mapping": MATRIX_MAPPING,
            "source_sha256": hashlib.sha256(content).hexdigest(),
            "source_headers": sheet.headers,
            "source_rows": len(sheet.rows),
        },
        citations=[],
        decided_by=actor.id,
    )
    db_session.add(mapping_proposal)
    await db_session.commit()
    proposal_id = mapping_proposal.id

    resp = await client.post(
        "/api/matrix/import", headers=headers,
        files={"file": ("x.xlsx", content, _XLSX)},
        data={"proposal_id": str(proposal_id)},
    )

    body = resp.json()
    assert body["code"] == "matrix.import_invalid"
    assert isinstance(body["message"], str) and body["message"]
    assert body["params"]["count"] == len(report)
    assert body["params"]["issues"] == [asdict(issue) for issue in report]
    assert [entry["code"] for entry in body["params"]["issues"]] == [issue.code for issue in report]

    # 事务性：校验失败一条 CONTROL_EXTRACT 提案都没建（路由 rollback 后依旧为 0）。
    # 种子那条 MATRIX_MAPPING 先 commit 到 SAVEPOINT，不受路由 rollback 影响。
    control_extracts = await db_session.scalar(
        select(func.count()).select_from(Proposal).where(
            Proposal.kind == ProposalKind.CONTROL_EXTRACT
        )
    )
    assert control_extracts == 0
    # 种子提案仍在（导入失败不该把它掀掉）。
    assert await db_session.scalar(
        select(func.count()).select_from(Proposal).where(
            Proposal.kind == ProposalKind.MATRIX_MAPPING
        )
    ) == 1
