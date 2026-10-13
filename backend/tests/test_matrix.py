"""纯测试：**刻意**不去要 `db_session` / `client` / `_schema` 这几个 fixture。"""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook

from app.errors import Conflict, install_error_handlers
from app.iam.models import AuditLog
from app.llm.runner import ValidatedResult
from app.matrix.importer import import_confirmed, import_rows, validate_mapping_proposal
from app.matrix.mapping import propose
from app.matrix.template import (
    CANONICAL_FIELDS,
    REQUIRED_FIELDS,
    Sheet,
    SheetRejected,
    read_sheet,
    validate,
)
from app.review.models import Proposal, ProposalKind, ProposalStatus


def workbook(headers=None, rows=None) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(headers if headers is not None else ["编号", "名称", "描述", "责任人", "框架", "备注"])
    for row in rows if rows is not None else [["AC-1", "复核", "每季复核", "IT", "CSF", "保留"]]:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


MAPPING = {"code": "编号", "title": "名称", "statement": "描述", "owner": "责任人",
           "framework_refs": "框架", "note": "备注"}


def session_mock() -> Mock:
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


def mapping_proposal(content: bytes, **kwargs) -> Proposal:
    sheet = read_sheet(content)
    return Proposal(id=1, kind=ProposalKind.MATRIX_MAPPING,
                    status=kwargs.pop("status", ProposalStatus.ACCEPTED),
                    payload={"mapping": MAPPING, "source_sha256": hashlib.sha256(content).hexdigest(),
                             "source_headers": sheet.headers, "source_rows": len(sheet.rows)},
                    **kwargs)


def test_canonical_fields():
    assert CANONICAL_FIELDS == ("code", "title", "statement", "category", "owner",
                                "framework_refs", "note")
    assert REQUIRED_FIELDS == ("title", "statement")


def test_read_and_sample():
    content = workbook(["A", "B"], [["x", "y"], ["z", "w"]])
    assert read_sheet(content) == Sheet(["A", "B"], [["x", "y"], ["z", "w"]])
    assert read_sheet(content, max_rows=1).rows == [["x", "y"]]
    assert read_sheet(content, max_rows=0).rows == []
    with pytest.raises(ValueError):
        read_sheet(content, max_rows=-1)


@pytest.mark.parametrize("content", [b"", b"not excel", b"x" * (10 * 1024 * 1024 + 1)])
def test_reject_invalid_or_oversized(content):
    with pytest.raises(ValueError, match="Excel"):
        read_sheet(content)


@pytest.mark.parametrize("value", ["=1+1", "#DIV/0!"])
def test_reject_formulas_and_errors(value):
    with pytest.raises(SheetRejected) as caught:
        read_sheet(workbook(["A"], [[value]]))
    assert caught.value.code == "matrix.formula_cells"


def test_numeric_zero_and_false_are_not_empty():
    sheet = read_sheet(workbook(["A", "B"], [[0, False]]))
    assert sheet.rows == [["0", "False"]]


@pytest.mark.parametrize("headers,rows,mapping,code,params", [
    (["A", "B"], [["x", "y"]], {}, "matrix.required_field_unmapped", {"field": "title"}),
    (["A", "B"], [["x", "y"]], {"title": "NO", "statement": "B"}, "matrix.column_missing", {"column": "NO"}),
    (["A", "A"], [["x", "y"]], {"title": "A", "statement": "A"}, "matrix.header_duplicate", {}),
    (["A", ""], [["x", "y"]], {"title": "A", "statement": "A"}, "matrix.header_blank", {}),
    (["A", "B"], [["", "y"]], {"title": "A", "statement": "B"}, "matrix.required_field_blank",
     {"count": 1, "field": "title"}),
    (["A", "B"], [], {"title": "A", "statement": "B"}, "matrix.no_rows", {}),
    (["A", "B"], [["x" * 501, "y"]], {"title": "A", "statement": "B"}, "matrix.field_too_long",
     {"field": "title", "limit": 500}),
    (["A", "B"], [["x", "y", "hidden"]], {"title": "A", "statement": "B"}, "matrix.data_without_header", {}),
])
def test_validation(headers, rows, mapping, code, params):
    # 报告是 code + 参数，前端按语言组句；中文 detail 只是兜底。
    assert (code, params) in [(issue.code, issue.params) for issue in validate(Sheet(headers, rows), mapping)]


@pytest.mark.parametrize("mapping", [None, [], "bad", {"title": []}])
def test_mapping_requires_string_object(mapping):
    assert validate(Sheet(["A"], [["x"]]), mapping)


def test_duplicates_and_valid_mapping():
    assert validate(read_sheet(workbook()), MAPPING) == []
    sheet = read_sheet(workbook(rows=[["AC-1", "x", "y"], ["AC-1", "z", "w"]]))
    assert [(issue.code, issue.params) for issue in validate(sheet, MAPPING)] == [
        ("matrix.duplicate_codes", {"codes": ["AC-1"]}),
    ]


def replace_zip(content: bytes, name: str, transform) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(out, "w") as target:
        for entry in source.infolist():
            value = source.read(entry)
            target.writestr(entry.filename, transform(value) if entry.filename == name else value)
    return out.getvalue()


def test_lying_dimensions_do_not_expand_iteration():
    content = replace_zip(workbook(), "xl/worksheets/sheet1.xml",
                          lambda raw: raw.replace(b'A1:F2', b'A1:XFD1048576'))
    assert len(read_sheet(content).rows) == 1


def test_out_of_bounds_cell_rejected_before_openpyxl():
    content = replace_zip(workbook(), "xl/worksheets/sheet1.xml",
                          lambda raw: raw.replace(b'r="A2"', b'r="XFD2"'))
    with pytest.raises(SheetRejected, match="行列") as caught:
        read_sheet(content)
    assert caught.value.code == "matrix.sheet_too_large"


def test_entity_declaration_rejected():
    content = replace_zip(workbook(), "xl/workbook.xml",
                          lambda raw: b'<!DOCTYPE x [<!ENTITY x "boom">]>' + raw)
    with pytest.raises(SheetRejected) as caught:
        read_sheet(content)
    assert caught.value.code == "matrix.xml_dtd"


def test_archive_inflation_limit(monkeypatch):
    from app.matrix import template
    monkeypatch.setattr(template, "MAX_EXPANDED_BYTES", 100)
    with pytest.raises(SheetRejected) as caught:
        read_sheet(workbook())
    assert caught.value.code == "matrix.archive_too_large"


async def test_ai_only_sees_five_samples_and_binding_is_server_supplied():
    content = workbook(["A", "B"], [[f"r{i}", f"v{i}"] for i in range(500)])
    result = ValidatedResult({"mapping": {"title": "A", "statement": "B"},
                              "source_sha256": "evil", "file_path": "/etc/passwd"}, .88, 4)
    session = session_mock()
    with patch("app.matrix.mapping.run", AsyncMock(return_value=result)) as run:
        proposal = await propose(session, read_sheet(content), source_sha256="a" * 64)
    prompt = run.call_args.kwargs["prompt"]
    assert "r499" not in prompt and "r5" not in prompt and "r0" in prompt
    assert proposal.kind == ProposalKind.MATRIX_MAPPING
    assert proposal.status == ProposalStatus.PENDING
    assert proposal.confidence == .88
    assert proposal.payload["source_sha256"] == "a" * 64
    assert "file_path" not in proposal.payload


@pytest.mark.parametrize("change", [{"source_sha256": "b" * 64}, {"source_headers": ["other"]},
                                     {"source_rows": 42}, {"file_path": "/etc/passwd"},
                                     {"upload_id": "evil"}])
def test_review_cannot_replace_source_binding(change):
    proposal = mapping_proposal(workbook())
    with pytest.raises(ValueError):
        validate_mapping_proposal(proposal, {"mapping": MAPPING, **change})


def test_review_allows_mapping_only_modification():
    assert validate_mapping_proposal(mapping_proposal(workbook()), {"mapping": MAPPING}) == MAPPING


async def test_import_only_creates_pending_proposals_and_audit_preserving_optional_fields():
    session = session_mock()
    result = await import_rows(session, read_sheet(workbook()), MAPPING, actor_id=5,
                               mapping_proposal_id=1, source_sha256="a" * 64)
    assert result["imported"] == 1
    proposal, audit = session.added
    assert isinstance(proposal, Proposal) and isinstance(audit, AuditLog)
    assert proposal.status == ProposalStatus.PENDING
    assert proposal.kind == ProposalKind.CONTROL_EXTRACT
    assert proposal.confidence is None and proposal.citations == []
    assert {key: proposal.payload[key] for key in ("code", "owner", "framework_refs", "note")} == {
        "code": "AC-1", "owner": "IT", "framework_refs": "CSF", "note": "保留"}
    assert proposal.payload["row_number"] == 1
    assert audit.action == "matrix.import" and audit.after == result
    session.commit.assert_not_called()


async def test_validation_failure_writes_nothing():
    session = session_mock()
    with pytest.raises(ValueError):
        await import_rows(session, read_sheet(workbook()), {}, actor_id=5,
                          mapping_proposal_id=1, source_sha256="a" * 64)
    session.add.assert_not_called()


@pytest.mark.parametrize("status", [ProposalStatus.PENDING, ProposalStatus.REJECTED])
async def test_unconfirmed_mapping_cannot_import(status):
    content = workbook()
    session = session_mock()
    session.scalar.return_value = mapping_proposal(content, status=status)
    with pytest.raises(Conflict):
        await import_confirmed(session, read_sheet(content), content,
                               proposal_id=1, actor=SimpleNamespace(id=5))
    session.add.assert_not_called()


async def test_changed_file_or_mapping_cannot_import():
    content = workbook()
    session = session_mock()
    session.scalar.return_value = mapping_proposal(content)
    for uploaded, mapping in ((content + b"changed", None), (content, {"title": "other"})):
        with pytest.raises(Conflict):
            await import_confirmed(session, read_sheet(content), uploaded, proposal_id=1,
                                   actor=SimpleNamespace(id=5), mapping=mapping)
    session.add.assert_not_called()


async def test_modified_mapping_import_and_retry_are_idempotent():
    content = workbook()
    changed = {**MAPPING, "title": "描述", "statement": "名称"}
    proposal = mapping_proposal(content, status=ProposalStatus.MODIFIED,
                                decided_payload={"mapping": changed})
    original = dict(proposal.payload)
    session = session_mock()
    session.scalar.side_effect = [proposal, None]
    result = await import_confirmed(session, read_sheet(content), content,
                                    proposal_id=1, actor=SimpleNamespace(id=5))
    assert session.added[0].payload["title"] == "每季复核"
    assert proposal.payload == original
    assert "FOR UPDATE" in str(session.scalar.call_args_list[0].args[0])
    session.scalar.side_effect = [proposal, session.added[-1]]
    retry = await import_confirmed(session, read_sheet(content), content,
                                   proposal_id=1, actor=SimpleNamespace(id=5))
    assert retry == result and len(session.added) == 2


def test_matrix_never_constructs_formal_tables():
    for path in (Path(__file__).parents[1] / "app" / "matrix").glob("*.py"):
        text = path.read_text()
        assert not any(f"{name}(" in text for name in ("Control", "ControlSource", "ControlRelation"))


async def test_validate_route_is_multipart_and_handles_malformed_mapping():
    from app.iam.deps import current_user
    from app.iam.permissions import Role
    from app.matrix.router import router
    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=5, role=Role.GRC_LEAD)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for mapping_json, expected in ((json.dumps(MAPPING), 200), ("{bad", 400), ("[]", 400)):
            response = await client.post("/api/matrix/validate", files={"file": ("x.xlsx", workbook())},
                                         data={"mapping_json": mapping_json})
            assert response.status_code == expected
        assert (await client.post("/api/matrix/validate", files={"file": ("x.txt", b"x")},
                                  data={"mapping_json": "{}"})).status_code == 400


async def test_matrix_routes_answer_with_codes_not_sentences():
    # 后端不产出给人读的句子：原来校验报告是一串中文，拒绝也只给 request_rejected
    # 加一句中文 detail，英文界面整段显示中文。
    from app.iam.deps import current_user
    from app.iam.permissions import Role
    from app.matrix.router import router
    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=5, role=Role.GRC_LEAD)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        report = (await client.post(
            "/api/matrix/validate", files={"file": ("x.xlsx", workbook())},
            data={"mapping_json": json.dumps({"title": "名称", "statement": "不存在的列"})},
        )).json()
        assert report["ok"] is False
        assert [(line["code"], line["params"]) for line in report["report"]] == [
            ("matrix.column_missing", {"column": "不存在的列"}),
        ]
        rejected = (await client.post(
            "/api/matrix/validate", files={"file": ("x.txt", b"x")}, data={"mapping_json": "{}"},
        )).json()
        assert rejected["code"] == "matrix.xlsx_only"
        malformed = (await client.post(
            "/api/matrix/validate", files={"file": ("x.xlsx", workbook())}, data={"mapping_json": "{bad"},
        )).json()
        assert malformed["code"] == "matrix.mapping_not_json"


async def test_propose_failure_keeps_llm_trace():
    from fastapi import UploadFile

    from app.errors import AppError
    from app.llm.providers.base import ProviderError
    from app.llm.validation import ValidationFailure
    from app.matrix.router import propose_mapping
    for error in (ProviderError("offline", retryable=True), ValidationFailure("bad schema")):
        session = session_mock()
        with (patch("app.matrix.mapping.run", AsyncMock(side_effect=error)),
              pytest.raises(AppError, match="调用记录已保留")):
            await propose_mapping(UploadFile(io.BytesIO(workbook()), filename="x.xlsx"),
                                  actor=SimpleNamespace(id=5), session=session)
        session.commit.assert_awaited_once()
        session.rollback.assert_not_called()
        session.add.assert_not_called()


@pytest.mark.parametrize("role", ["viewer", "contributor"])
async def test_matrix_write_routes_enforce_permissions_without_database(role):
    from app.db import get_session
    from app.iam.deps import current_user
    from app.iam.permissions import Role
    from app.matrix.router import router
    app = FastAPI()
    app.include_router(router)
    install_error_handlers(app)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=5, role=Role(role))
    app.dependency_overrides[get_session] = session_mock
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for endpoint in ("propose-mapping", "validate", "import"):
            response = await client.post(f"/api/matrix/{endpoint}",
                                         files={"file": ("x.xlsx", workbook())},
                                         data={"mapping_json": "{}", "proposal_id": "1"})
            assert response.status_code == 403
