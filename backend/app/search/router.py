from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.search.schemas import SearchResponse
from app.search.service import search as run_search

# 查询会被送去做模型扩展并参与全文/向量检索；不设上限等于允许任意大的 prompt。
MAX_QUERY_LENGTH = 500

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query(default="", max_length=MAX_QUERY_LENGTH),
    limit: int = Query(default=20, ge=1, le=100),
    document_id: int | None = None,
    user: User = Depends(require(Permission.READ)),
    session: AsyncSession = Depends(get_session),
) -> SearchResponse:
    return await run_search(
        session, q, limit=limit, document_id=document_id, user_id=user.id
    )
