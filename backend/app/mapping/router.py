from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import Conflict, NotFound
from app.frameworks.models import Framework
from app.iam.audit import record
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.worker import enqueue

router = APIRouter(prefix="/api/mapping", tags=["mapping"])


@router.post("/frameworks/{framework_id}")
async def start(
    framework_id: Annotated[int, Path(gt=0)],
    actor: Annotated[User, Depends(require(Permission.FRAMEWORK_WRITE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    if await session.get(Framework, framework_id) is None:
        raise NotFound("框架不存在", code="framework.not_found")
    job_id = await enqueue("map_framework", framework_id)
    if not job_id:
        raise Conflict("映射任务未能进入队列", code="task.mapping_enqueue_failed")
    await record(
        session,
        user=actor,
        action="mapping.enqueue",
        entity_type="Framework",
        entity_id=framework_id,
        after={"job_id": job_id},
    )
    await session.commit()
    return {"job_id": job_id}
