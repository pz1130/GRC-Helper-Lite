"""暂存区清理（OQ-4）。

上传先落 /tmp（compose 里的 staging 卷），`parse_document` 把它搬进
`/data/documents` 之后再删。搬运之前 worker 死掉、或文档行被删掉，那个文件
就再没有任何东西会来收——卷只进不出。

只删**没有文档行指着**且过了保留期的文件：还被指着的可能正等着 reparse，
刚落盘的可能正排在队列里。宁可多留一天，也不要删掉还能用的原文。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.models import Document
from app.ingest.storage import ALLOWED_EXTENSIONS

STAGING_ROOT = Path("/tmp")
RETENTION = timedelta(hours=24)


async def sweep(
    session: AsyncSession,
    *,
    root: Path = STAGING_ROOT,
    retention: timedelta = RETENTION,
    now: datetime | None = None,
) -> dict[str, int]:
    """删掉暂存区里无主且过期的上传文件，返回这一轮的处置计数。"""
    summary = {"removed": 0, "bytes": 0, "referenced": 0, "recent": 0}
    if not root.is_dir():
        return summary

    referenced = set(await session.scalars(select(Document.file_path)))
    cutoff = ((now or datetime.now(UTC)) - retention).timestamp()

    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        if str(path) in referenced:
            summary["referenced"] += 1
            continue
        stat = path.stat()
        if stat.st_mtime >= cutoff:
            summary["recent"] += 1
            continue
        path.unlink(missing_ok=True)
        summary["removed"] += 1
        summary["bytes"] += stat.st_size

    return summary


async def purge_staging(ctx: dict[str, Any]) -> dict[str, int]:
    """定时任务入口。幂等：无主又过期的文件删一次就不在了。"""
    del ctx
    from app.db import session_factory

    async with session_factory() as session:
        return await sweep(session)
