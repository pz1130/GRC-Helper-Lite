"""判断模型必须单独启用；普通聊天路由与全局备用模型不能启用它。"""

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.decision.factory import supports_decisions
from app.errors import BadRequest
from app.llm.models import AppSetting, LLMProviderConfig, TaskRouting

TASK_KEY = "requirement_decision"
SETTING_KEY = "decision_model"


class DecisionSettings(BaseModel):
    enabled: bool = False
    provider_config_id: int | None = Field(default=None, gt=0)


async def read_settings(session: AsyncSession) -> DecisionSettings:
    setting = await session.get(AppSetting, SETTING_KEY, populate_existing=True)
    route = await session.scalar(select(TaskRouting).where(TaskRouting.task_key == TASK_KEY).execution_options(populate_existing=True))
    return DecisionSettings(
        enabled=bool(setting and setting.value.get("enabled") is True),
        provider_config_id=route.provider_config_id if route else None,
    )


async def save_settings(session: AsyncSession, payload: DecisionSettings) -> None:
    config = (await session.get(LLMProviderConfig, payload.provider_config_id)
              if payload.provider_config_id else None)
    if payload.provider_config_id and (not config or not supports_decisions(config.kind)):
        raise BadRequest("请选择判断模型服务", code="decision.provider_required")
    if payload.enabled and (not config or not config.enabled):
        raise BadRequest("启用前必须配置可用的判断模型服务", code="decision.provider_required")
    route = await session.scalar(select(TaskRouting).where(TaskRouting.task_key == TASK_KEY))
    if config:
        if route is None:
            route = TaskRouting(task_key=TASK_KEY, provider_config_id=config.id)
            session.add(route)
        route.provider_config_id = config.id
    elif route:
        await session.delete(route)
    setting = await session.get(AppSetting, SETTING_KEY)
    if setting is None:
        setting = AppSetting(key=SETTING_KEY, value={})
        session.add(setting)
    setting.value = {"enabled": payload.enabled}
    await session.flush()
