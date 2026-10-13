import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.llm.models import AppSetting, LLMCall, LLMProviderConfig

logger = logging.getLogger(__name__)

WARN_RATIO = 0.8


class BudgetExceeded(AppError):
    """预算耗尽。

    是 AppError 的子类而不是裸 Exception：批量任务按 AppError 分类处理，
    裸 Exception 会被当成"未知故障"直接把任务打挂，而用户那边什么都看不到
    ——界面只说过"已排队"。带 code 之后，确认队列里能留下一行说明原因的记录。
    """

    status_code = 409
    code = "budget.exhausted"


async def month_to_date_cost(
    session: AsyncSession, *, provider_config_id: int | None = None
) -> float:
    now = datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    stmt = select(func.coalesce(func.sum(LLMCall.cost), 0.0)).where(LLMCall.at >= start)
    if provider_config_id is not None:
        stmt = stmt.where(LLMCall.provider_config_id == provider_config_id)
    total = await session.scalar(stmt)
    return float(total or 0.0)


async def uncosted_calls(session: AsyncSession) -> int:
    """本月有多少次调用根本没算进成本（模型不在价格表里）。

    这个数字大于 0 时，`month_to_date_cost` 是**下限**而不是实际花费，
    预算闸门也就不可信。界面要把它说出来，不能只显示一个漂亮的 $0.00。
    """
    now = datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = await session.scalar(
        select(func.count()).where(LLMCall.at >= start, LLMCall.cost_unknown.is_(True))
    )
    return int(total or 0)


async def _budget(session: AsyncSession) -> float | None:
    setting = await session.get(AppSetting, "monthly_budget_usd")
    if setting is None:
        return None
    value = setting.value.get("value")
    return float(value) if value else None


def _enforce(provider: str | None, budget: float, spent: float, *, interactive: bool) -> None:
    """spec §9：达 80% 告警；达 100% 暂停批量任务，但保留交互式任务。

    `provider` 为 None 是全局预算。两者各有各的 code：原来共用一个，把
    「provider「名字」」这段中文当参数拼在英文句子开头。
    """
    scope = f"provider {provider} " if provider else ""
    if spent >= budget:
        if not interactive:
            amounts = {"spent": f"{spent:.2f}", "budget": f"{budget:.2f}"}
            if provider:
                raise BudgetExceeded(
                    f"provider「{provider}」本月 AI 调用已达预算上限"
                    f"（{spent:.2f} / {budget:.2f} USD），批量任务已暂停",
                    code="budget.provider_exhausted",
                    params={"provider": provider, **amounts},
                )
            raise BudgetExceeded(
                f"本月 AI 调用已达预算上限（{spent:.2f} / {budget:.2f} USD），批量任务已暂停",
                code="budget.exhausted",
                params=amounts,
            )
        logger.warning("%sbudget exhausted, interactive call allowed: %.2f / %.2f USD", scope, spent, budget)
    elif spent >= budget * WARN_RATIO:
        logger.warning(
            "%sAI spend at %.0f%% of budget: %.2f / %.2f USD",
            scope, spent / budget * 100, spent, budget,
        )


async def check(
    session: AsyncSession,
    *,
    interactive: bool,
    provider: LLMProviderConfig | None = None,
) -> None:
    """全局预算和 provider 级预算都要过，任一超标即拦。

    provider 级预算原本是个只存不看的死字段——设置页把它显示成一个开关，
    实际却从不生效，这比没有更糟。
    """
    global_budget = await _budget(session)
    if global_budget is not None and global_budget > 0:
        _enforce(
            None,
            global_budget,
            await month_to_date_cost(session),
            interactive=interactive,
        )

    if provider is not None and provider.monthly_budget and provider.monthly_budget > 0:
        _enforce(
            provider.name,
            float(provider.monthly_budget),
            await month_to_date_cost(session, provider_config_id=provider.id),
            interactive=interactive,
        )
