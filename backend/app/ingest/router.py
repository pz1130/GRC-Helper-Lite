import tempfile
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.clauses.schemas import ClauseTreeOut
from app.conflicts.models import PolicyConflict
from app.controls.models import ControlSource
from app.db import get_session
from app.errors import AppError, Conflict, NotFound, PayloadTooLarge
from app.iam.audit import record
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.ingest.coverage import document_coverage, uncovered_clauses
from app.ingest.models import DocStatus, DocType, Document
from app.ingest.schemas import (
    DocumentCoverageOut,
    DocumentMetaIn,
    DocumentOut,
    PlainTextIn,
    UncoveredClauseOut,
)
from app.ingest.service import find_by_hash, supersede_previous
from app.ingest.storage import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, sha256_of
from app.ingest.warnings import to_codes
from app.parsing.contract import ParseWarning
from app.worker import enqueue

router = APIRouter(prefix="/api/documents", tags=["documents"])

# Annotated 形式的依赖别名。本文件其余端点沿用旧的 `= Depends(...)` 默认值写法
# （ruff 的 B008 对它们都有告警），新写的端点用这两个别名，不再添新的告警。
MetaWriter = Annotated[User, Depends(require(Permission.DOCUMENT_WRITE))]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{document_id}/source-blocks")
async def original_blocks(
    document_id: int,
    _: Annotated[User, Depends(require(Permission.READ))],
    session: Session,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    unassigned_only: bool = False,
) -> dict:
    from app.clauses.sources import source_page

    return await source_page(session, document_id, offset=offset, limit=limit,
                             unassigned_only=unassigned_only)


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(...),
    doc_type: DocType = Form(...),
    supersedes_id: int | None = Form(None),
    actor: User = Depends(require(Permission.DOCUMENT_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> Document:
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise AppError(
            f"不支持的文件类型 {extension or '(无扩展名)'}。"
            "Excel 控制矩阵导入属于 M4，目前只支持 .pdf 与 .docx。",
            code="ingest.unsupported_file_type",
            params={"extension": extension or "(none)"},
        )

    # 多读一个字节就够判断超限，不必把整份超大文件读进内存。
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge(
            f"文件过大（上限 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB）。",
            code="document.upload_too_large",
            params={"limit_mb": MAX_UPLOAD_BYTES // (1024 * 1024)},
        )
    digest = sha256_of(content)
    existing = await find_by_hash(session, digest)
    if existing is not None:
        raise Conflict(
            f"该文件已存在（文档 #{existing.id}：{existing.title}）",
            code="document.duplicate_upload",
            params={"id": existing.id, "title": existing.title},
        )

    if supersedes_id is not None:
        # 新版本只能接在「现行、且还没有后继」的那一版后面——supersede_previous 只撤
        # supersedes_id 那一份，接错地方两版就会同时是现行版本。锁住旧版这一行，同时
        # 到达的两次上传排队检查，不会都看到「还没有后继」。
        previous = await session.get(
            Document, supersedes_id, with_for_update=True, populate_existing=True
        )
        if previous is None:
            raise NotFound("上一版本文档不存在", code="document.previous_version_not_found")
        if previous.status is DocStatus.SUPERSEDED:
            raise Conflict(
                "这一版已被新版替换，新版本请接在现行版后面上传",
                code="document.previous_version_superseded",
            )
        if previous.status is not DocStatus.ACTIVE:
            # 接在一份没生效过的文档后面：新版成功时撤掉的是它，真正的现行版没人撤。
            raise Conflict(
                "这一版还没有生效，新版本请接在现行版后面上传",
                code="document.previous_version_not_active",
            )
        successor = await session.scalar(
            select(Document).where(Document.supersedes_id == previous.id).order_by(Document.id).limit(1)
        )
        if successor is not None:
            # 已有的后继解析失败了，就在它上面重新解析或粘贴文本，而不是再接一个。
            raise Conflict(
                f"这一版已经有新版本（文档 #{successor.id}：{successor.title}）",
                code="document.previous_version_has_successor",
                params={"id": successor.id, "title": successor.title},
            )

    with tempfile.NamedTemporaryFile(delete=False, suffix=extension, dir="/tmp") as handle:
        handle.write(content)
        staged = handle.name

    document = Document(
        title=title,
        doc_type=doc_type,
        status=DocStatus.UPLOADED,
        original_filename=file.filename or f"upload{extension}",
        file_hash=digest,
        file_path=staged,
        uploaded_by=actor.id,
        supersedes_id=supersedes_id,
    )
    session.add(document)
    await session.flush()

    await record(
        session,
        user=actor,
        action="document.upload",
        entity_type="Document",
        entity_id=document.id,
        after={"title": title, "doc_type": doc_type.value, "filename": document.original_filename},
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    await enqueue("parse_document", document.id)
    return document


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    status_filter: DocStatus | None = Query(default=None, alias="status"),
    doc_type: DocType | None = None,
    _: User = Depends(require(Permission.READ)),
    session: AsyncSession = Depends(get_session),
) -> list[Document]:
    statement = select(Document).order_by(Document.id.desc())
    if status_filter:
        statement = statement.where(Document.status == status_filter)
    if doc_type:
        statement = statement.where(Document.doc_type == doc_type)
    return list(await session.scalars(statement))


@router.get("/coverage", response_model=list[DocumentCoverageOut])
async def coverage(
    _: User = Depends(require(Permission.READ)),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentCoverageOut]:
    """你自己的制度里，有多少要求还没变成控制点。

    与框架覆盖度方向相反：那个问「外部要求有没有被满足」，这个问「我们写下的
    要求有没有被系统看见」。两种盲区都是静默的——不扫就没有任何地方会提示。
    """
    return [
        DocumentCoverageOut(**{**row.__dict__, "never_extracted": row.never_extracted})
        for row in await document_coverage(session)
    ]


@router.get("/{document_id}/uncovered", response_model=list[UncoveredClauseOut])
async def uncovered(
    document_id: int,
    _: User = Depends(require(Permission.READ)),
    session: AsyncSession = Depends(get_session),
) -> list[UncoveredClauseOut]:
    return [UncoveredClauseOut(**row.__dict__)
            for row in await uncovered_clauses(session, document_id)]


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: int,
    _: User = Depends(require(Permission.READ)),
    session: AsyncSession = Depends(get_session),
) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        raise NotFound("文档不存在", code="document.not_found")
    return document


@router.patch("/{document_id}", response_model=DocumentOut)
async def update_document_meta(
    document_id: int,
    body: DocumentMetaIn,
    actor: MetaWriter,
    session: Session,
) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        raise NotFound("文档不存在", code="document.not_found")

    changes = body.model_dump(exclude_unset=True)
    before = {field: _audit_value(getattr(document, field)) for field in changes}
    for field, value in changes.items():
        setattr(document, field, value)
    await session.flush()
    await record(
        session,
        user=actor,
        action="document.meta_update",
        entity_type="Document",
        entity_id=document.id,
        before=before,
        after={field: _audit_value(getattr(document, field)) for field in changes},
    )
    await session.commit()
    return document


async def _ensure_clauses_unreferenced(session: AsyncSession, document: Document) -> None:
    """重建条款树之前确认没有已确认数据指着旧条款。

    `persist` 先删光本文档的条款再重建，而 control_sources 与 policy_conflicts
    对条款是 ON DELETE CASCADE——不拦的话，已确认的出处和冲突会被数据库悄悄
    删掉，既绕过 materialize，也不留审计。条款已经被用上的文档要改，走上传
    新版本（supersedes）那条路，由变更影响分析接手。
    """
    own = select(Clause.id).where(Clause.document_id == document.id)
    sources = await session.scalar(
        select(func.count()).select_from(ControlSource).where(ControlSource.clause_id.in_(own))
    )
    conflicts = await session.scalar(
        select(func.count())
        .select_from(PolicyConflict)
        .where(or_(PolicyConflict.clause_a_id.in_(own), PolicyConflict.clause_b_id.in_(own)))
    )
    if sources or conflicts:
        raise Conflict(
            f"该文档的条款已被 {sources} 条控制点出处、{conflicts} 条冲突引用，"
            "重新生成条款会把它们一并删掉。请上传新版本替换。",
            code="document.clauses_referenced",
            params={"sources": sources, "conflicts": conflicts},
        )


def _audit_value(value: object) -> object:
    return value.isoformat() if isinstance(value, date) else value


@router.get("/{document_id}/clauses", response_model=list[ClauseTreeOut])
async def get_clauses(
    document_id: int,
    _: User = Depends(require(Permission.READ)),
    session: AsyncSession = Depends(get_session),
) -> list[ClauseTreeOut]:
    rows = list(
        await session.scalars(
            select(Clause)
            .where(Clause.document_id == document_id, Clause.status != "merged")
            .order_by(Clause.order_index)
        )
    )
    nodes = {row.id: ClauseTreeOut.model_validate(row) for row in rows}
    roots: list[ClauseTreeOut] = []
    for row in rows:
        node = nodes[row.id]
        parent = nodes.get(row.parent_id) if row.parent_id else None
        (parent.children if parent else roots).append(node)
    return roots


@router.post("/{document_id}/plain-text", response_model=DocumentOut)
async def replace_with_plain_text(
    document_id: int,
    payload: PlainTextIn,
    request: Request,
    actor: User = Depends(require(Permission.DOCUMENT_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> Document:
    """用人工粘贴的编号文本给解析失败的文档兜底：按编号切成条款。"""
    from app.parsing.flatten import persist
    from app.parsing.numbering import assemble_tree, extract_headings
    from app.parsing.unstructured import structure_warnings
    from app.parsing.contract import SourceBlock

    document = await session.get(Document, document_id)
    if document is None:
        raise NotFound("文档不存在", code="document.not_found")
    await _ensure_clauses_unreferenced(session, document)

    lines = payload.text.splitlines()
    headings, warnings = extract_headings(lines)
    nodes = assemble_tree(headings, lines)
    warnings.extend(structure_warnings(nodes))
    blocks = [SourceBlock(key=f"line:{index}", text=line, kind="line",
                          locator={"line": index + 1})
              for index, line in enumerate(lines) if line.strip()]
    count = await persist(session, nodes, document_id=document.id,
                          source_blocks=blocks, source_origin="pasted_text")

    document.status = DocStatus.ACTIVE
    document.parse_error = None
    document.parse_error_code = None
    document.parse_error_params = None
    tail = ParseWarning(
        f"由人工粘贴的纯文本生成 {count} 条条款",
        code="parse.from_pasted_text",
        params={"count": count},
    )
    document.parse_warnings = "\n".join([*warnings, tail])
    document.parse_warning_codes = to_codes([*warnings, tail])
    await session.flush()
    await supersede_previous(session, document, actor=actor)

    await record(
        session,
        user=actor,
        action="document.plain_text_fallback",
        entity_type="Document",
        entity_id=document.id,
        after={"clauses": count},
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    await enqueue("index_document", document.id)
    return document


@router.post("/{document_id}/reparse", response_model=DocumentOut)
async def reparse(
    document_id: int,
    request: Request,
    actor: User = Depends(require(Permission.DOCUMENT_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        raise NotFound("文档不存在", code="document.not_found")
    if document.status is DocStatus.SUPERSEDED:
        # 解析成功会把状态写成 active：重新解析旧版等于把它复活成现行版本。
        raise Conflict("该文档已被新版本替换，不能重新解析", code="document.superseded")
    await _ensure_clauses_unreferenced(session, document)
    document.status = DocStatus.UPLOADED
    document.parse_error = None
    document.parse_error_code = None
    document.parse_error_params = None
    await record(
        session,
        user=actor,
        action="document.reparse",
        entity_type="Document",
        entity_id=document.id,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    await enqueue("parse_document", document.id)
    return document
