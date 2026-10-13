from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import Conflict, NotFound
from app.iam.audit import record
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.ingest.models import DocStatus, Document
from app.worker import enqueue

router = APIRouter(prefix="/api/extraction", tags=["extraction"])


@router.get("/documents/{document_id}/inventories/{inventory_id}")
async def inventory_detail(
    document_id: Annotated[int, Path(gt=0)],
    inventory_id: Annotated[int, Path(gt=0)],
    actor: Annotated[User, Depends(require(Permission.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    from app.extraction.models import ExtractionInventory

    inventory = await session.get(ExtractionInventory, inventory_id)
    if inventory is None or inventory.document_id != document_id:
        raise NotFound("要求清单不存在", code="extraction.inventory_not_found")
    return {"id": inventory.id, "llm_call_id": inventory.llm_call_id,
            "created_at": inventory.created_at.isoformat(), **inventory.payload,
            "normalization_call_id": inventory.normalization_call_id,
            "semantic_review": inventory.semantic_review, "review_call_id": inventory.review_call_id,
            "normalized_controls": (inventory.normalized_payload or {}).get("controls", [])}


@router.get("/documents/{document_id}/coverage")
async def coverage(
    document_id: Annotated[int, Path(gt=0)],
    actor: Annotated[User, Depends(require(Permission.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    from app.extraction.coverage import extraction_coverage

    if await session.get(Document, document_id) is None:
        raise NotFound("文档不存在", code="document.not_found")
    return await extraction_coverage(session, document_id)


@router.post("/documents/{document_id}")
async def extract(
    document_id: Annotated[int, Path(gt=0)],
    actor: Annotated[User, Depends(require(Permission.CONTROL_WRITE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    document = await session.get(Document, document_id)
    if document is None:
        raise NotFound("文档不存在", code="document.not_found")
    if document.status != DocStatus.ACTIVE:
        raise Conflict("只有 active 文档可以抽取控制点", code="document.not_active_for_extraction")
    job_id = await enqueue("extract_controls", document_id)
    if not job_id:
        raise Conflict("抽取任务未能进入队列", code="task.extraction_enqueue_failed")
    await record(
        session, user=actor, action="extraction.enqueue", entity_type="Document",
        entity_id=document_id, after={"job_id": job_id},
    )
    await session.commit()
    return {"job_id": job_id}
