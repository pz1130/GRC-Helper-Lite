"""接收可信来源的指标观测：串行处理同一指标，保存原始观测并为红色区间登记事件。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict
from app.iam.models import User
from app.rcsa import triggers
from app.rcsa.access import audit, bump, check_version, fetch, lock
from app.rcsa.models import RcsaKri, RcsaKriObservation, RcsaTrigger, RcsaTriggerControl
from app.rcsa.schemas import (
    KriIn,
    KriObservationIn,
    KriObservationOut,
    KriOut,
    KriStateIn,
    TriggerControlIn,
)


async def latest(session: AsyncSession, kri_id: int) -> RcsaKriObservation | None:
    return await session.scalar(select(RcsaKriObservation).where(RcsaKriObservation.kri_id == kri_id)
        .order_by(RcsaKriObservation.observed_at.desc(), RcsaKriObservation.id.desc()).limit(1))


async def view(session: AsyncSession, rule: RcsaKri) -> KriOut:
    result = KriOut.model_validate(rule)
    row = await latest(session, rule.id)
    result.latest = KriObservationOut.model_validate(row) if row else None
    return result


async def listing(session: AsyncSession, limit: int, offset: int) -> tuple[list[KriOut], int]:
    rules = list(await session.scalars(select(RcsaKri).order_by(RcsaKri.id.desc()).limit(limit).offset(offset)))
    # 每页批量取最新观测，避免指标数量带来逐条查询。
    ranked = select(RcsaKriObservation.id, func.row_number().over(
        partition_by=RcsaKriObservation.kri_id,
        order_by=(RcsaKriObservation.observed_at.desc(), RcsaKriObservation.id.desc()),
    ).label("position")).where(RcsaKriObservation.kri_id.in_([r.id for r in rules])).subquery()
    rows = list(await session.scalars(select(RcsaKriObservation).where(
        RcsaKriObservation.id.in_(select(ranked.c.id).where(ranked.c.position == 1)))))
    by_rule = {r.kri_id: KriObservationOut.model_validate(r) for r in rows}
    return [KriOut.model_validate(r).model_copy(update={"latest": by_rule.get(r.id)}) for r in rules], int(
        await session.scalar(select(func.count()).select_from(RcsaKri)) or 0)


async def create(session: AsyncSession, payload: KriIn, actor: User) -> RcsaKri:
    from app.review.materialize import lock_control_writes

    await lock_control_writes(session)
    await triggers._owner(session, payload.owner_id)
    target = await triggers._control_row(session, 0, TriggerControlIn(
        control_id=payload.control_id, implementation_id=payload.implementation_id))
    rule = RcsaKri(**payload.model_dump(), target_snapshot={
        "control": target.control_snapshot, "implementation": target.implementation_snapshot,
    }, created_by=actor.id, updated_by=actor.id)
    session.add(rule)
    await session.flush()
    await audit(session, actor, "kri_create", "RcsaKri", rule.id, payload.model_dump(mode="json"))
    return rule


async def configure(session: AsyncSession, kri_id: int, payload: KriStateIn, actor: User) -> RcsaKri:
    rule = await lock(session, RcsaKri, kri_id, code="rcsa.kri_not_found")
    check_version(rule, payload.expected_version)
    await triggers._owner(session, payload.owner_id)
    rule.enabled, rule.owner_id = payload.enabled, payload.owner_id
    bump(rule, actor)
    await session.flush()
    await audit(session, actor, "kri_configure", "RcsaKri", rule.id, payload.model_dump(mode="json"))
    return rule


async def ingest(session: AsyncSession, kri_id: int, payload: KriObservationIn, actor: User) -> RcsaKriObservation:
    rule = await lock(session, RcsaKri, kri_id, code="rcsa.kri_not_found")
    previous = await session.scalar(select(RcsaKriObservation).where(
        RcsaKriObservation.kri_id == rule.id, RcsaKriObservation.external_id == payload.external_id))
    if previous:
        if (previous.observed_at, previous.value, previous.source_ref) != (payload.observed_at, payload.value, payload.source_ref):
            raise Conflict("同一来源编号带了不同内容", code="rcsa.kri_replay_conflict")
        return previous
    if not rule.enabled:
        raise Conflict("指标未启用", code="rcsa.kri_disabled")
    now = datetime.now(UTC)
    if payload.observed_at > now:
        raise Conflict("观测时间不能在未来", code="rcsa.kri_future_observation")
    last = await latest(session, rule.id)
    if last and payload.observed_at <= last.observed_at:
        raise Conflict("观测必须晚于上一条，新编号不能覆盖历史", code="rcsa.kri_out_of_order")
    def reached(threshold):
        return payload.value >= threshold if rule.direction == "high" else payload.value <= threshold
    band = "red" if reached(rule.red_threshold) else "amber" if reached(rule.amber_threshold) else "green"
    trigger_id = None
    if band == "red":
        if last and last.band == "red":
            trigger_id = last.trigger_id
        else:
            await triggers._owner(session, rule.owner_id)
            trigger = RcsaTrigger(
                trigger_type="kri", origin_kri_id=rule.id, title=f"KRI: {rule.name}",
                description=f"Observed {payload.value} {rule.unit}; red threshold {rule.red_threshold} ({rule.direction}).",
                source_ref=payload.source_ref, occurred_at=payload.observed_at,
                owner_id=rule.owner_id, decision_due_at=now.date() + timedelta(days=rule.decision_days),
                status="registered", created_by=actor.id, updated_by=actor.id,
            )
            session.add(trigger)
            await session.flush()
            session.add(RcsaTriggerControl(trigger_id=trigger.id, control_id=rule.control_id,
                implementation_id=rule.implementation_id,
                control_snapshot=rule.target_snapshot["control"],
                implementation_snapshot=rule.target_snapshot["implementation"]))
            trigger_id = trigger.id
            await audit(session, actor, "kri_trigger", "RcsaTrigger", trigger.id, {"kri_id": rule.id, "external_id": payload.external_id})
    row = RcsaKriObservation(kri_id=rule.id, **payload.model_dump(), band=band, trigger_id=trigger_id,
        received_by=actor.id, rule_snapshot={
            "name": rule.name, "source": rule.source, "unit": rule.unit, "direction": rule.direction,
            "amber_threshold": str(rule.amber_threshold), "red_threshold": str(rule.red_threshold),
            "owner_id": rule.owner_id, "decision_days": rule.decision_days, "version": rule.version,
            "target": rule.target_snapshot,
        })
    session.add(row)
    await session.flush()
    await audit(session, actor, "kri_observe", "RcsaKriObservation", row.id,
                {"kri_id": rule.id, "external_id": row.external_id, "band": band, "trigger_id": trigger_id})
    await session.refresh(row)
    return row


async def history(session: AsyncSession, kri_id: int, limit: int, offset: int):
    await fetch(session, RcsaKri, kri_id, code="rcsa.kri_not_found")
    query = select(RcsaKriObservation).where(RcsaKriObservation.kri_id == kri_id)
    rows = list(await session.scalars(query.order_by(RcsaKriObservation.observed_at.desc()).limit(limit).offset(offset)))
    total = int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    return rows, total
