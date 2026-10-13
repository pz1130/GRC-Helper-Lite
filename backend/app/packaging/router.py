from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.packaging.service import build_package

router = APIRouter(prefix="/api/frameworks", tags=["frameworks"])


@router.get("/{framework_id}/readiness-package")
async def readiness_package(
    *,
    framework_id: int,
    language: str = "en",
    _: Annotated[User, Depends(require(Permission.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    content = await build_package(session, framework_id, language=language)
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="readiness-{framework_id}.zip"'
        },
    )
