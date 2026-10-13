"""异步的文档解析任务。"""

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_factory
from app.ingest.models import DocStatus, DocType, Document
from app.ingest.service import supersede_previous
from app.ingest.storage import save
from app.ingest.warnings import to_codes
from app.parsing.contract import ParseError, ParseWarning
from app.parsing.flatten import persist
from app.parsing.registry import get_parser
from app.parsing.shape import assess_shape
from app.parsing.validate import check_completeness

STAGING = Path("/tmp")


def _record_failure(document: Document, exc: Exception) -> None:
    """记下失败原因：原文兜底 + code 与变量（界面按 code 查词条）。"""
    document.status = DocStatus.PARSE_FAILED
    if isinstance(exc, ParseError):
        document.parse_error = exc.reason
        document.parse_error_code = exc.code
        document.parse_error_params = exc.params
        return
    document.parse_error = f"解析时发生未预期的错误：{type(exc).__name__}: {exc}"
    document.parse_error_code = "parse_error.unexpected"
    document.parse_error_params = {"error": type(exc).__name__}


async def run_parse(session: AsyncSession, document: Document) -> dict[str, Any]:
    """把解析从 ARQ 里摘出来单独跑，好让它能被直接测试。"""
    document.status = DocStatus.PARSING
    document.parse_error = None
    document.parse_error_code = None
    document.parse_error_params = None
    await session.flush()

    try:
        path = Path(document.file_path)
        if path.parent == STAGING:
            # 暂存文件在这里**不删**：之后写库一旦失败、事务回滚，file_path 会
            # 回到暂存路径，那时它必须还在。删除由 parse_document 在提交之后做。
            stored = save(path.read_bytes(), document.original_filename)
            document.file_path = stored.path
            await session.flush()
            path = Path(document.file_path)
        parsed = get_parser(path).parse(path)
    except Exception as exc:
        _record_failure(document, exc)
        await session.flush()
        raise

    # 形状检查在导入时就说话：切成一坨的文档不该静悄悄地变成 active。
    warnings = [*parsed.warnings, *assess_shape(parsed), *check_completeness(parsed)]
    count = await persist(session, parsed.clauses, document_id=document.id,
                          source_blocks=parsed.source_blocks, source_origin=parsed.source_origin)

    meta = parsed.meta
    for field in ("version", "owner", "approver"):
        value = getattr(meta, field)
        if not value:
            continue
        limit = Document.__table__.c[field].type.length
        if len(value) > limit:
            # 封面值是从任意文档的行里切出来的，没有长度上限。放不下的多半不是
            # 真正的版本号/负责人，丢掉并说出来，而不是截断成半句或拖垮整个解析。
            warnings.append(
                ParseWarning(
                    f"封面字段 {field} 超过 {limit} 个字符，未采用",
                    code="parse.cover_field_too_long",
                    params={"field": field, "limit": limit},
                )
            )
            continue
        setattr(document, field, value)
    document.approved_date = meta.approved_date or document.approved_date
    document.effective_date = meta.effective_date or document.effective_date
    if meta.doc_type:
        document.doc_type = DocType(meta.doc_type)

    document.ocr_quality_flag = parsed.ocr_used
    document.parse_warnings = "\n".join(warnings) if warnings else None
    document.parse_warning_codes = to_codes(warnings)
    document.status = DocStatus.ACTIVE
    await session.flush()
    await supersede_previous(session, document)

    return {"clauses": count, "warnings": warnings}


async def parse_document(ctx: dict[str, Any], document_id: int) -> dict[str, Any]:
    """ARQ 任务入口。"""
    # try/finally 必须在 async with **内部**：session 一 close，document 就变成
    # detached 对象，对它的所有赋值（status、file_path、封面元数据）在 flush 时
    # 会被静默丢弃——而 persist() 新 add 的 Clause 照常落库，所以看起来"解析成功了"，
    # 实际文档状态永远停在 uploaded、file_path 还指向已被删掉的 /tmp 路径。
    async with session_factory() as session:
        document = await session.get(Document, document_id)
        if document is None:
            return {"clauses": 0, "warnings": [f"文档 {document_id} 不存在"]}
        staged = Path(document.file_path)
        try:
            result = await run_parse(session, document)
            await session.commit()
        except Exception as exc:
            # 失败可能发生在写库时（事务已坏），run_parse 记下的 parse_failed 随
            # 回滚一起没了。回滚后重新记一次，文档才不会停在 uploaded/parsing。
            # file_path 也随之回到暂存路径——暂存文件还没删，所以仍然可以重新解析。
            await session.rollback()
            failed = await session.get(Document, document_id)
            if failed is not None:
                _record_failure(failed, exc)
                await session.commit()
            raise
        if staged.parent == STAGING and document.file_path != str(staged):
            staged.unlink(missing_ok=True)

    # 解析成功后自动接上分块与向量化，避免用户手动触发第二个任务。
    from app.worker import enqueue

    await enqueue("index_document", document_id)
    return result
