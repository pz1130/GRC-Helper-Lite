from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.iam.audit import record
from app.iam.models import User
from app.ingest.models import DocStatus, DocType, Document
from app.ingest.storage import StoredFile


async def find_by_hash(session: AsyncSession, sha256: str) -> Document | None:
    return await session.scalar(select(Document).where(Document.file_hash == sha256))


async def register(
    session: AsyncSession,
    *,
    stored: StoredFile,
    original_filename: str,
    title: str,
    doc_type: DocType,
    uploaded_by: int | None,
    supersedes_id: int | None = None,
) -> Document:
    document = Document(
        title=title,
        doc_type=doc_type,
        status=DocStatus.UPLOADED,
        original_filename=original_filename,
        file_hash=stored.sha256,
        file_path=stored.path,
        uploaded_by=uploaded_by,
        supersedes_id=supersedes_id,
    )
    session.add(document)
    await session.flush()
    return document


async def supersede_previous(
    session: AsyncSession, document: Document, *, actor: User | None = None
) -> None:
    """新版有了条款树之后，把它声明替换的旧版撤下来。

    只在新版**成功**生成条款之后调用：新版解析失败时，旧版仍是唯一可用的现行版本。
    旧版上已确认的控制点出处原样保留——那是历史，变更影响分析也要靠它。
    撤下之后抽取不再接受它（只认 active），覆盖扫描也不再把它的条款算作盲区。
    """
    if document.supersedes_id is None:
        return
    previous = await session.get(Document, document.supersedes_id)
    if previous is None or previous.status is DocStatus.SUPERSEDED:
        return
    before = previous.status.value
    previous.status = DocStatus.SUPERSEDED
    await session.flush()
    await record(
        session,
        user=actor,
        action="document.superseded",
        entity_type="Document",
        entity_id=previous.id,
        before={"status": before},
        after={"status": DocStatus.SUPERSEDED.value, "superseded_by": document.id},
    )
