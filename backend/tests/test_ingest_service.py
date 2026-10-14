import pytest

from app.ingest.models import DocStatus, DocType
from app.ingest.service import find_by_hash, register, supersede_previous
from app.ingest.storage import StoredFile

STORED = StoredFile(path="/data/documents/ab/abcd.pdf", sha256="ab" + "c" * 62, size=10)


async def _register(db_session, **kwargs):
    defaults = {
        "stored": STORED,
        "original_filename": "proc.pdf",
        "title": "IT Change Management Procedure",
        "doc_type": DocType.PROCEDURE,
        "uploaded_by": None,
    }
    defaults.update(kwargs)
    return await register(db_session, **defaults)


@pytest.mark.asyncio
async def test_register_creates_uploaded_document(db_session):
    doc = await _register(db_session)
    assert doc.status is DocStatus.UPLOADED
    assert doc.file_hash == STORED.sha256
    assert doc.file_path == STORED.path


@pytest.mark.asyncio
async def test_find_by_hash_detects_duplicate(db_session):
    await _register(db_session)
    assert await find_by_hash(db_session, STORED.sha256) is not None
    assert await find_by_hash(db_session, "f" * 64) is None


@pytest.mark.asyncio
async def test_superseding_marks_the_old_version(db_session):
    old = await _register(db_session)
    old.status = DocStatus.ACTIVE
    await db_session.flush()

    newer = StoredFile(path="/data/documents/cd/cdef.pdf", sha256="cd" + "e" * 62, size=11)
    new = await _register(
        db_session, stored=newer, original_filename="proc-v2.pdf", supersedes_id=old.id
    )
    await supersede_previous(db_session, new)

    assert old.status is DocStatus.SUPERSEDED
    assert new.supersedes_id == old.id
