"""两次上传的接口：提案与来源文件的绑定不可变，且不接受客户端传来的文件路径。"""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.db import get_session
from app.errors import AppError
from app.iam.audit import record
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.llm.providers.base import ProviderError
from app.llm.validation import ValidationFailure
from app.matrix.importer import import_confirmed
from app.matrix.mapping import propose
from app.matrix.template import MAX_FILE_BYTES, Sheet, SheetRejected, read_sheet, validate
from app.review.models import Proposal
from app.review.schemas import ProposalOut

router = APIRouter(prefix="/api/matrix", tags=["matrix"])
MatrixActor = Annotated[User, Depends(require(Permission.CONTROL_WRITE))]
MatrixSession = Annotated[AsyncSession, Depends(get_session)]


async def _read_upload(file: UploadFile) -> tuple[bytes, Sheet]:
    if Path(file.filename or "").suffix.lower() != ".xlsx":
        raise SheetRejected("仅支持 .xlsx Excel 文件", code="matrix.xlsx_only")
    content = await file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise SheetRejected(
            "Excel 文件超过 10 MiB 限制", code="matrix.file_too_large",
            params={"limit_mib": MAX_FILE_BYTES // (1024 * 1024)},
        )
    return content, await run_in_threadpool(read_sheet, content)


def _parse_mapping(value: str) -> dict[str, str]:
    if len(value) > 64 * 1024:
        raise SheetRejected("列映射超过大小限制", code="matrix.mapping_too_large")
    try:
        mapping = json.loads(value)
    except (ValueError, RecursionError) as exc:
        raise SheetRejected("列映射不是有效 JSON", code="matrix.mapping_not_json") from exc
    if not isinstance(mapping, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()
    ):
        raise SheetRejected("列映射必须为字符串对象", code="matrix.mapping_not_object")
    return mapping


def _rejected(exc: ValueError) -> AppError:
    """带 code 的拒绝原样交给前端；剩下的只可能是内部不变量被破坏，给通用 code。"""
    if isinstance(exc, SheetRejected):
        return AppError(str(exc), code=exc.code, params=exc.params)
    return AppError(str(exc), code="matrix.request_rejected", params={"detail": str(exc)})


@router.post("/propose-mapping", response_model=ProposalOut)
async def propose_mapping(
    file: Annotated[UploadFile, File()], actor: MatrixActor, session: MatrixSession,
) -> Proposal:
    try:
        content, sheet = await _read_upload(file)
        proposal = await propose(session, sheet, source_sha256=hashlib.sha256(content).hexdigest())
        await record(session, user=actor, action="matrix.propose_mapping", entity_type="Proposal",
                     entity_id=proposal.id, after={"rows": len(sheet.rows)})
        await session.commit()
        return proposal
    except (ProviderError, ValidationFailure) as exc:
        # runner 已经把 LLMCall 的错误记录刷进去了，即使没有映射提案也要留着。
        # 这个请求没有别的业务写入。
        await session.commit()
        raise AppError("列映射生成失败；调用记录已保留，请检查模型配置后重试", code="matrix.mapping_failed") from exc
    except ValueError as exc:
        await session.rollback()
        raise _rejected(exc) from exc


@router.post("/validate")
async def validate_mapping(
    file: Annotated[UploadFile, File()], mapping_json: Annotated[str, Form()], _: MatrixActor,
) -> dict[str, Any]:
    try:
        _, sheet = await _read_upload(file)
        report = validate(sheet, _parse_mapping(mapping_json))
        return {"ok": not report, "report": [asdict(issue) for issue in report], "rows": len(sheet.rows)}
    except ValueError as exc:
        raise _rejected(exc) from exc


@router.post("/import")
async def run_import(
    file: Annotated[UploadFile, File()], proposal_id: Annotated[int, Form(gt=0)],
    actor: MatrixActor, session: MatrixSession,
    mapping_json: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    try:
        content, sheet = await _read_upload(file)
        mapping = _parse_mapping(mapping_json) if mapping_json is not None else None
        result = await import_confirmed(
            session, sheet, content, proposal_id=proposal_id, actor=actor, mapping=mapping,
        )
        await session.commit()
        return result
    except (ValueError, AppError) as exc:
        await session.rollback()
        if isinstance(exc, AppError):
            raise
        raise _rejected(exc) from exc
