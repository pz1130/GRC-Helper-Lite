from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.controls.models import RelationType
from app.db import get_session
from app.errors import BadRequest
from app.graph import service
from app.graph.schemas import GraphOut
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission

router = APIRouter(prefix="/api/graph", tags=["graph"])

Reader = Annotated[User, Depends(require(Permission.READ))]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/relations", response_model=GraphOut)
async def relation_graph(
    *,
    focus: str | None = None,
    hops: int = Query(default=1, ge=1, le=2),
    include_pending: bool = False,
    types: str | None = None,
    framework_id: int | None = None,
    _: Reader,
    session: Session,
) -> GraphOut:
    parsed: set[RelationType] | None = None
    if types:
        try:
            parsed = {RelationType(value) for value in types.split(",") if value}
        except ValueError as exc:
            raise BadRequest("types 只接受 duplicates / depends_on / conflicts_with", code="relations.types_invalid") from exc
    return await service.relation_graph(
        session,
        focus=focus,
        hops=hops,
        include_pending=include_pending,
        types=parsed,
        framework_id=framework_id,
    )


@router.get("/mappings", response_model=GraphOut)
async def mapping_graph(
    *,
    framework_id: int,
    focus: str | None = None,
    hops: int = Query(default=1, ge=1, le=2),
    include_pending: bool = False,
    only_gaps: bool = False,
    _: Reader,
    session: Session,
) -> GraphOut:
    return await service.mapping_graph(
        session,
        framework_id=framework_id,
        focus=focus,
        hops=hops,
        include_pending=include_pending,
        only_gaps=only_gaps,
    )
