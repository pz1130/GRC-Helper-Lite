"""保存读取快照，重解析相同输入复用原文块，新的输入保留旧快照。"""

import hashlib
import json
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import OriginalBlock
from app.ingest.models import Document
from app.parsing.contract import SourceBlock


async def persist_blocks(
    session: AsyncSession, document_id: int, blocks: list[SourceBlock], *, origin: str,
) -> dict[str, int]:
    if len({block.key for block in blocks}) != len(blocks):
        raise ValueError("source block keys must be unique within a snapshot")
    snapshot = hashlib.sha256(json.dumps(
        [origin, [asdict(block) for block in blocks]],
        sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()
    values = [{
        "document_id": document_id, "snapshot": snapshot, "source_key": block.key,
        "order_index": index, "origin": origin, "kind": block.kind,
        "text": block.text, "locator": block.locator,
    } for index, block in enumerate(blocks)]
    for start in range(0, len(values), 500):
        await session.execute(insert(OriginalBlock).values(values[start:start + 500])
                              .on_conflict_do_nothing(constraint="uq_original_block_key"))
    document = await session.get(Document, document_id)
    if document is None:
        raise ValueError("source snapshot requires an existing document")
    document.source_snapshot = snapshot
    await session.flush()
    rows = await session.execute(select(OriginalBlock.source_key, OriginalBlock.id).where(
        OriginalBlock.document_id == document_id, OriginalBlock.snapshot == snapshot,
    ))
    return dict(rows.all())
