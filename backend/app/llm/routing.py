from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.decision.factory import ADAPTERS
from app.errors import AppError
from app.llm.models import LLMProviderConfig, TaskRouting


class RoutingError(AppError):
    """每次 raise 带具体 code：「没配 provider」是新部署第一个撞上的错误。"""


async def resolve(session: AsyncSession, task_key: str) -> tuple[LLMProviderConfig, TaskRouting]:
    routing = await session.scalar(select(TaskRouting).where(TaskRouting.task_key == task_key))
    if routing is None:
        raise RoutingError(
            f"任务 {task_key} 尚未配置 provider，请到设置页配置",
            code="task.not_routed", params={"task": task_key},
        )

    config = await session.get(LLMProviderConfig, routing.provider_config_id)
    if config is None or not config.enabled:
        raise RoutingError(
            f"任务 {task_key} 绑定的 provider 不可用",
            code="task.provider_unavailable", params={"task": task_key},
        )
    return config, routing


async def fallback_provider(session: AsyncSession) -> LLMProviderConfig | None:
    return await session.scalar(
        select(LLMProviderConfig).where(
            LLMProviderConfig.is_fallback.is_(True), LLMProviderConfig.enabled.is_(True),
            LLMProviderConfig.kind.notin_(list(ADAPTERS))
        )
    )


async def routed_model(session: AsyncSession, task_key: str) -> str | None:
    """这个任务眼下路由到的模型名；没配置或不可用时是 None（交给 run 去报错）。

    批量任务的检查点指纹带上它：同样的输入换了模型，是一次新的判断，要重跑。
    """
    try:
        config, _ = await resolve(session, task_key)
    except RoutingError:
        return None
    return config.model
