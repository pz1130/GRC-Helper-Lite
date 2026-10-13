from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.db import get_session
from app.errors import AppError, NotFound
from app.frameworks import coverage as coverage_module
from app.frameworks import service
from app.frameworks.importer import import_framework, read_template
from app.frameworks.models import Framework
from app.frameworks.schemas import (
    CoverageOut,
    FrameworkItemOut,
    FrameworkOut,
    GapOut,
    SupersededSupportOut,
)
from app.frameworks.template import parse_rows
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.matrix.template import MAX_FILE_BYTES, SheetRejected

router = APIRouter(prefix="/api/frameworks", tags=["frameworks"])
Reader = Annotated[User, Depends(require(Permission.READ))]
Writer = Annotated[User, Depends(require(Permission.FRAMEWORK_WRITE))]
Session = Annotated[AsyncSession, Depends(get_session)]


async def _read(file: UploadFile) -> tuple[list[str], list[list[str]]]:
    content = await file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise AppError("文件超过 10 MiB 限制", code="framework.file_too_large")
    return await run_in_threadpool(read_template, content)


def _rejected(exc: ValueError) -> AppError:
    """读表的拒绝（read_sheet）自带 code，原样交给前端；其余给通用 code。"""
    if isinstance(exc, SheetRejected):
        return AppError(str(exc), code=exc.code, params=exc.params)
    return AppError(str(exc), code="framework.import_rejected", params={"detail": str(exc)})


async def _require_framework(session: AsyncSession, framework_id: int) -> Framework:
    framework = await session.get(Framework, framework_id)
    if framework is None:
        raise NotFound("框架不存在", code="framework.not_found")
    return framework


@router.get("", response_model=list[FrameworkOut])
async def list_all(_: Reader, session: Session) -> list[Framework]:
    return await service.list_frameworks(session)


@router.get("/{framework_id}/tree", response_model=list[FrameworkItemOut])
async def tree(
    framework_id: Annotated[int, Path(gt=0)],
    _: Reader,
    session: Session,
) -> list[Any]:
    await _require_framework(session, framework_id)
    return await service.tree(session, framework_id)


@router.post("/validate")
async def validate(file: Annotated[UploadFile, File()], _: Writer) -> dict[str, Any]:
    try:
        headers, rows = await _read(file)
    except ValueError as exc:
        raise _rejected(exc) from exc
    parsed, report = parse_rows(headers, rows)
    return {"ok": not report, "report": [asdict(issue) for issue in report], "items": len(parsed)}


@router.post("/import", response_model=FrameworkOut)
async def run_import(
    file: Annotated[UploadFile, File()],
    key: Annotated[str, Form(min_length=1, max_length=64)],
    name_en: Annotated[str, Form(min_length=1, max_length=200)],
    version: Annotated[str, Form(min_length=1, max_length=32)],
    source: Annotated[str, Form(max_length=500)],
    actor: Writer,
    session: Session,
    # 产品只有英文界面（2026-09-23 起）：中文名不再必填。
    name_zh: Annotated[str, Form(max_length=200)] = "",
) -> Framework:
    try:
        headers, rows = await _read(file)
    except ValueError as exc:
        await session.rollback()
        raise _rejected(exc) from exc
    framework = await import_framework(
        session,
        headers,
        rows,
        key=key,
        name_zh=name_zh,
        name_en=name_en,
        version=version,
        source=source,
        actor=actor,
    )
    await session.commit()
    return framework


@router.get("/{framework_id}/coverage", response_model=list[CoverageOut])
async def coverage(
    framework_id: Annotated[int, Path(gt=0)],
    _: Reader,
    session: Session,
    baseline: str | None = Query(default=None, max_length=32),
) -> list[Any]:
    await _require_framework(session, framework_id)
    return await coverage_module.summarize(session, framework_id, baseline=baseline)


@router.get("/{framework_id}/superseded-support", response_model=list[SupersededSupportOut])
async def superseded_support(
    framework_id: Annotated[int, Path(gt=0)],
    _: Reader,
    session: Session,
    baseline: str | None = Query(default=None, max_length=32),
) -> list[Any]:
    await _require_framework(session, framework_id)
    return await coverage_module.superseded_support(session, framework_id, baseline=baseline)


@router.get("/{framework_id}/gaps", response_model=list[GapOut])
async def gaps(
    framework_id: Annotated[int, Path(gt=0)],
    _: Reader,
    session: Session,
    baseline: str | None = Query(default=None, max_length=32),
) -> list[Any]:
    await _require_framework(session, framework_id)
    return await coverage_module.gaps(session, framework_id, baseline=baseline)
