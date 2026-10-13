from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.impact.schemas import ImpactOut
from app.impact.service import change_impact

router = APIRouter(prefix="/api/documents", tags=["documents"])

Reader = Annotated[User, Depends(require(Permission.READ))]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{document_id}/change-impact", response_model=ImpactOut)
async def get_change_impact(document_id: int, _: Reader, session: Session) -> ImpactOut:
    return await change_impact(session, document_id)
