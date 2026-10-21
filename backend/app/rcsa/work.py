"""站内待办：按查询时刻计算，不依赖邮件或定时任务。

逾期是派生出来的，不覆盖业务状态。这是一个聚合视图，不是授权边界——READ 仍然是
内部 IT 的全局只读，「我的待办」只是把与我有关的事排在一起。
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.iam.models import User
from app.iam.permissions import Permission, has_permission
from app.rcsa.models import (
    ActionStatus,
    CampaignStatus,
    Disposition,
    DispositionStatus,
    ItemStatus,
    RcsaAction,
    RcsaCampaign,
    RcsaItem,
    RcsaItemRisk,
    RcsaReview,
    RcsaTrigger,
    ReviewDecision,
    TriggerStatus,
)
from app.rcsa.schemas import WorkRow

# 接受在到期前多少天开始提醒。
ACCEPTANCE_NOTICE = timedelta(days=30)
_LIVE = (CampaignStatus.ACTIVE, CampaignStatus.IN_REVIEW)


def _control(item: RcsaItem) -> str:
    snapshot = item.control_snapshot or {}
    label = f"{snapshot.get('code', '')} {snapshot.get('title', '')}".strip()
    implementation = item.implementation_snapshot
    if implementation:
        asset = implementation.get("asset")
        target = f"{asset['name']} · {asset['environment']}" if asset else "Process implementation"
        label += f" · {target}"
    return label


def _row(kind: str, row_id: int, title: str, status: object, due: date | None, today: date,
         campaign: RcsaCampaign | None = None, item_id: int | None = None) -> WorkRow:
    return WorkRow(
        kind=kind, id=row_id, title=title, status=str(status), due_at=due, item_id=item_id,
        overdue=due is not None and due < today,
        campaign_id=campaign.id if campaign else None, campaign_name=campaign.name if campaign else None,
    )


async def my_work(session: AsyncSession, actor: User) -> list[WorkRow]:
    today = datetime.now(UTC).date()
    rows: list[WorkRow] = []

    items = await session.execute(
        select(RcsaItem, RcsaCampaign)
        .join(RcsaCampaign, RcsaCampaign.id == RcsaItem.campaign_id)
        .where(
            RcsaCampaign.status == CampaignStatus.ACTIVE,
            or_(
                (RcsaItem.assignee_id == actor.id)
                & RcsaItem.status.in_([ItemStatus.PENDING, ItemStatus.DRAFT, ItemStatus.RETURNED]),
                (RcsaItem.reviewer_id == actor.id) & (RcsaItem.status == ItemStatus.SUBMITTED),
            ),
        )
    )
    for item, campaign in items:
        kind = "review" if item.status == ItemStatus.SUBMITTED else "assessment"
        rows.append(_row(kind, item.id, _control(item), item.status, campaign.due_at, today, campaign, item.id))

    risk_rows = select(RcsaItemRisk, RcsaItem, RcsaCampaign).join(
        RcsaItem, RcsaItem.id == RcsaItemRisk.item_id
    ).join(RcsaCampaign, RcsaCampaign.id == RcsaItem.campaign_id)
    to_propose = await session.execute(risk_rows.where(
        RcsaCampaign.status.in_(_LIVE),
        RcsaItem.status == ItemStatus.APPROVED,
        RcsaItemRisk.disposition_status.in_([DispositionStatus.PENDING, DispositionStatus.RETURNED]),
        or_(RcsaItemRisk.owner_id == actor.id, RcsaItem.assignee_id == actor.id),
    ))
    for risk, item, campaign in to_propose:
        rows.append(_row("disposition", risk.id, risk.event, risk.disposition_status, campaign.due_at, today,
                         campaign, item.id))
    if has_permission(actor.role, Permission.RCSA_RISK_ACCEPT):
        to_approve = await session.execute(risk_rows.where(
            RcsaCampaign.status.in_(_LIVE),
            RcsaItemRisk.disposition_status == DispositionStatus.PROPOSED,
            RcsaItemRisk.disposition_proposed_by != actor.id,
        ))
        for risk, item, campaign in to_approve:
            rows.append(_row("approval", risk.id, risk.event, risk.disposition_status, campaign.due_at, today,
                             campaign, item.id))

    # 接受到期提醒给风险责任人与最近一次批准它的人；到期不自动续期。
    approver = (
        select(RcsaReview.reviewer_id)
        .where(RcsaReview.item_risk_id == RcsaItemRisk.id,
               RcsaReview.decision == ReviewDecision.APPROVE_DISPOSITION)
        .order_by(RcsaReview.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    expiring = await session.execute(risk_rows.where(
        RcsaItemRisk.disposition == Disposition.ACCEPT,
        RcsaItemRisk.disposition_status == DispositionStatus.APPROVED,
        RcsaItemRisk.accept_until <= today + ACCEPTANCE_NOTICE,
        or_(RcsaItemRisk.owner_id == actor.id, approver == actor.id),
    ))
    for risk, item, campaign in expiring:
        status = "expired" if risk.accept_until < today else "expiring"
        rows.append(_row("acceptance", risk.id, risk.event, status, risk.accept_until, today, campaign, item.id))

    actions = await session.execute(
        select(RcsaAction, RcsaItem, RcsaCampaign)
        .join(RcsaItemRisk, RcsaItemRisk.id == RcsaAction.item_risk_id)
        .join(RcsaItem, RcsaItem.id == RcsaItemRisk.item_id)
        .join(RcsaCampaign, RcsaCampaign.id == RcsaItem.campaign_id)
        .where(or_(
            (RcsaAction.owner_id == actor.id)
            & RcsaAction.status.in_([ActionStatus.OPEN, ActionStatus.IN_PROGRESS]),
            (RcsaAction.verifier_id == actor.id) & (RcsaAction.status == ActionStatus.PENDING_VERIFICATION),
        ))
    )
    for action, item, campaign in actions:
        kind = "verification" if action.status == ActionStatus.PENDING_VERIFICATION else "action"
        rows.append(_row(kind, action.id, action.title, action.status, action.due_at, today, campaign, item.id))

    conditions = [
        (RcsaTrigger.owner_id == actor.id) & RcsaTrigger.status.in_(
            [TriggerStatus.REGISTERED, TriggerStatus.TRIAGED, TriggerStatus.AWAITING_ASSESSMENT]
        )
    ]
    if has_permission(actor.role, Permission.RCSA_REVIEW):
        conditions.append(
            (RcsaTrigger.status == TriggerStatus.NO_REASSESSMENT)
            & (func.coalesce(RcsaTrigger.proposed_by, 0) != actor.id)
        )
    for trigger in await session.scalars(select(RcsaTrigger).where(or_(*conditions))):
        kind = "trigger_review" if trigger.status == TriggerStatus.NO_REASSESSMENT else "trigger"
        rows.append(_row(kind, trigger.id, trigger.title, trigger.status, trigger.decision_due_at, today))

    return sorted(rows, key=lambda r: (not r.overdue, r.due_at or date.max, r.kind, r.id))
