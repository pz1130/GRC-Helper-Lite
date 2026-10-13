import logging
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.llm.models import AppSetting, LLMCall, LLMProviderConfig

logger = logging.getLogger(__name__)

WARN_RATIO = 0.8
# K5：批量任务的「检查 → 调模型 → 记账」原先不是原子的，并发批次都能在同一刻看到
# 「还没超」，一起放行。接近上限（≥ WARN_RATIO）后，批量调用改为排队：检查前先拿
# 事务级 advisory lock，锁一直持有到本批次提交（成本行随之可见），下一个批次再读到的
# 就是已经记上账的花费。远低于上限时不加锁，免得把所有批量任务串行化。
# 剩余误差：持锁期间单个批次自身的花费仍可能越过上限（最多超一个批次的成本）。
BUDGET_LOCK_KEY = 0x6772635F62756467  # "grc_budg"


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
    provider_budget = (
        float(provider.monthly_budget)
        if provider is not None and provider.monthly_budget and provider.monthly_budget > 0
        else None
    )
    if global_budget is not None and global_budget <= 0:
        global_budget = None
    if global_budget is None and provider_budget is None:
        return

    spent = await month_to_date_cost(session) if global_budget is not None else 0.0
    provider_spent = (
        await month_to_date_cost(session, provider_config_id=provider.id)
        if provider_budget is not None and provider is not None
        else 0.0
    )

    near_limit = (global_budget is not None and spent >= global_budget * WARN_RATIO) or (
        provider_budget is not None and provider_spent >= provider_budget * WARN_RATIO
    )
    if near_limit and not interactive:
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": BUDGET_LOCK_KEY})
        # 拿到锁后重读：排在前面的批次可能刚提交了它的花费。
        if global_budget is not None:
            spent = await month_to_date_cost(session)
        if provider_budget is not None and provider is not None:
            provider_spent = await month_to_date_cost(session, provider_config_id=provider.id)

    if global_budget is not None:
        _enforce(None, global_budget, spent, interactive=interactive)
    if provider_budget is not None and provider is not None:
        _enforce(provider.name, provider_budget, provider_spent, interactive=interactive)

