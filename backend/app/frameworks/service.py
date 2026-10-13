from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.frameworks.models import Framework, FrameworkItem


async def list_frameworks(session: AsyncSession) -> list[Framework]:
    return list(await session.scalars(select(Framework).order_by(Framework.key)))


async def tree(session: AsyncSession, framework_id: int) -> list[FrameworkItem]:
    return list(await session.scalars(
        select(FrameworkItem)
        .where(FrameworkItem.framework_id == framework_id)
        .order_by(FrameworkItem.order_index, FrameworkItem.id)
    ))
