"""暂存区清理。

上传先落 /tmp（staging 卷），worker 搬进 /data/documents。搬运发生之前进程
死掉、或文档行被删掉，暂存文件就没有任何东西会来收——那个卷只进不出（OQ-4）。
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.ingest.models import DocType, Document
from app.ingest.staging import RETENTION, sweep

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def _staged(root: Path, name: str, *, age: timedelta) -> Path:
    path = root / name
    path.write_bytes(b"%PDF-1.4 staged")
    stamp = (NOW - age).timestamp()
    os.utime(path, (stamp, stamp))
    return path


async def _document(db_session, path: Path) -> Document:
    document = Document(
        title=path.stem,
        doc_type=DocType.POLICY,
        file_hash=path.stem.ljust(64, "0"),
        file_path=str(path),
        original_filename=path.name,
    )
    db_session.add(document)
    await db_session.flush()
    return document


async def test_an_orphaned_staged_file_past_retention_is_removed(db_session, tmp_path):
    orphan = _staged(tmp_path, "dead.pdf", age=RETENTION + timedelta(hours=1))

    result = await sweep(db_session, root=tmp_path, now=NOW)

    assert not orphan.exists()
    assert result["removed"] == 1


async def test_a_staged_file_a_document_still_points_at_is_kept(db_session, tmp_path):
    staged = _staged(tmp_path, "waiting.pdf", age=RETENTION * 10)
    await _document(db_session, staged)

    result = await sweep(db_session, root=tmp_path, now=NOW)

    assert staged.exists(), "文档行还指着它，删了 reparse 就没得重来"
    assert result["removed"] == 0
    assert result["referenced"] == 1


async def test_a_fresh_orphan_is_left_alone(db_session, tmp_path):
    # 上传已落盘、parse_document 还没把它搬走的那几秒，文件就是无主的。
    inflight = _staged(tmp_path, "inflight.docx", age=timedelta(minutes=5))

    result = await sweep(db_session, root=tmp_path, now=NOW)

    assert inflight.exists()
    assert result["removed"] == 0


async def test_only_upload_extensions_at_the_top_level_are_touched(db_session, tmp_path):
    # /tmp 是共用卷，别的进程也往里写。只认自己放进去的那两类扩展名。
    other = _staged(tmp_path, "psql.log", age=RETENTION * 2)
    nested = tmp_path / "cache"
    nested.mkdir()
    buried = _staged(nested, "deep.pdf", age=RETENTION * 2)

    result = await sweep(db_session, root=tmp_path, now=NOW)

    assert other.exists() and buried.exists()
    assert result["removed"] == 0


async def test_a_missing_staging_root_is_not_an_error(db_session, tmp_path):
    result = await sweep(db_session, root=tmp_path / "nope", now=NOW)

    assert result == {"removed": 0, "bytes": 0, "referenced": 0, "recent": 0}
