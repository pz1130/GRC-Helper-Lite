from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import Conflict
from app.iam.audit import record
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.relations import params as relation_params
from app.relations.calibration import calibrate
from app.relations.indexing import embed_pending
from app.worker import enqueue

router = APIRouter(prefix="/api/relations", tags=["relations"])
Writer = Annotated[User, Depends(require(Permission.CONTROL_WRITE))]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/embed")
async def embed(actor: Writer, session: Session) -> dict[str, Any]:
    result = await embed_pending(session)
    await record(
        session, user=actor, action="relations.embed", entity_type="Control",
        entity_id=0, after=result,
    )
    await session.commit()
    return result


@router.post("/infer")
async def infer(actor: Writer, session: Session) -> dict[str, str]:
    job_id = await enqueue("infer_relations")
    if not job_id:
        raise Conflict("关系推断任务未能进入队列", code="task.relations_enqueue_failed")
    await record(
        session, user=actor, action="relations.enqueue", entity_type="Control",
        entity_id=0, after={"job_id": job_id},
    )
    await session.commit()
    return {"job_id": job_id}


@router.get("/params")
async def read_params(_: Writer, session: Session) -> dict[str, Any]:
    """当前生效的按语料标定参数（含未被覆盖时的回落默认值）。"""
    return asdict(await relation_params.load(session))


@router.post("/calibrate")
async def calibrate_threshold(actor: Writer, session: Session) -> dict[str, Any]:
    """按本语料自动标定 duplicates 的相似度阈值。

    标不出来就不写——沿用别人语料标出来的阈值比没有阈值更糟，它看起来像个结论。
    """
    result = await calibrate(session)
    applied = False
    if result.recommended is not None:
        await relation_params.save(session, "min_similarity", result.recommended)
        applied = True
        await record(
            session, user=actor, action="relations.calibrate", entity_type="AppSetting",
            entity_id="relation_min_similarity", after=result.as_dict(),
        )
    await session.commit()
    return {**result.as_dict(), "applied": applied}
