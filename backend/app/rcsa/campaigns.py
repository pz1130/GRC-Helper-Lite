"""活动、范围、分派、评分档案与激活（2A）。

激活在同一事务里**重新检查**范围，而不是相信创建时的结果：控制点可能已被合并、映射
可能已被删除、人员可能已停用。检查出的每个阻断项都带一个 code，前端逐条列出。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEngagement
from app.clauses.models import Clause
from app.controls.models import Control, ControlSource
from app.errors import Conflict, Forbidden, NotFound
from app.frameworks.models import Framework, FrameworkItem, Mapping
from app.iam.models import User
from app.iam.permissions import Permission, has_permission
from app.rcsa.access import Command, audit, bump, check_version, fetch, lock
from app.rcsa.models import (
    CampaignStatus,
    ItemStatus,
    ProfileStatus,
    ProviderType,
    RcsaCampaign,
    RcsaItem,
    RcsaScoringProfile,
)
from app.rcsa.profiles import default_bands, default_definitions, profile_problems
from app.rcsa.schemas import (
    AssignIn,
    CampaignCounts,
    CampaignCreateIn,
    CampaignOut,
    CampaignUpdateIn,
    ItemsAddIn,
    ProfileCreateIn,
    ProfileUpdateIn,
    ScopeRow,
)


def _now() -> datetime:
    return datetime.now(UTC)


# ── 人员 ────────────────────────────────────────────────────


async def _person(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("用户不存在", code="rcsa.user_not_found", params={"user_id": user_id})
    return user


async def _check_assignee(session: AsyncSession, user_id: int) -> User:
    user = await _person(session, user_id)
    if not user.is_active:
        raise Conflict("作答人已停用", code="rcsa.assignee_inactive")
    if not _can(user, Permission.RCSA_RESPOND):
        raise Conflict("作答人没有作答权限", code="rcsa.assignee_lacks_permission")
    return user


async def _check_reviewer(session: AsyncSession, user_id: int) -> User:
    user = await _person(session, user_id)
    if not user.is_active:
        raise Conflict("复核人已停用", code="rcsa.reviewer_inactive")
    if not _can(user, Permission.RCSA_REVIEW):
        raise Conflict("复核人没有复核权限", code="rcsa.reviewer_lacks_permission")
    return user


def _can(user: User, perm: Permission) -> bool:
    return has_permission(user.role, perm) and user.engagement_scope_id is None


def _distinct(assignee_id: int | None, reviewer_id: int | None) -> None:
    if assignee_id is not None and assignee_id == reviewer_id:
        raise Conflict("作答人不能同时是复核人", code="rcsa.reviewer_is_assignee")


def require_owner(campaign: RcsaCampaign, actor: User) -> None:
    if campaign.owner_id != actor.id:
        raise Forbidden("只有活动负责人能执行这个转换", code="rcsa.not_campaign_owner")


def require_status(campaign: RcsaCampaign, *allowed: CampaignStatus) -> None:
    if campaign.status not in allowed:
        if allowed == (CampaignStatus.DRAFT,):
            raise Conflict("活动已激活，范围不能再改", code="rcsa.campaign_not_draft")
        raise Conflict(
            "活动当前状态不允许这个操作",
            code="rcsa.campaign_state",
            params={"status": str(campaign.status)},
        )


# ── 评分档案 ────────────────────────────────────────────────


async def create_profile(
    session: AsyncSession, payload: ProfileCreateIn, actor: User
) -> RcsaScoringProfile:
    definitions, bands = default_definitions(), default_bands()
    if payload.base_profile_id is not None:
        base = await fetch(session, RcsaScoringProfile, payload.base_profile_id, code="rcsa.profile_not_found")
        definitions, bands = base.definitions, base.bands
    revision = (
        await session.scalar(
            select(func.max(RcsaScoringProfile.revision)).where(RcsaScoringProfile.key == payload.key)
        )
        or 0
    ) + 1
    profile = RcsaScoringProfile(
        key=payload.key, revision=revision, algorithm="product", definitions=definitions,
        bands=bands, horizon="", status=ProfileStatus.DRAFT, created_by=actor.id,
        updated_by=actor.id,
    )
    session.add(profile)
    await session.flush()
    await audit(session, actor, "profile_create", "RcsaScoringProfile", profile.id,
                {"key": profile.key, "revision": revision, "base_profile_id": payload.base_profile_id})
    return profile


async def update_profile(
    session: AsyncSession, profile_id: int, payload: ProfileUpdateIn, actor: User
) -> RcsaScoringProfile:
    profile = await lock(session, RcsaScoringProfile, profile_id, code="rcsa.profile_not_found")
    if profile.status != ProfileStatus.DRAFT:
        raise Conflict("已批准的评分版本不能修改，请新建修订版", code="rcsa.profile_immutable")
    check_version(profile, payload.expected_version)
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    for field, value in changes.items():
        setattr(profile, field, value)
    bump(profile, actor)
    await session.flush()
    await audit(session, actor, "profile_update", "RcsaScoringProfile", profile.id,
                {"fields": sorted(changes), "version": profile.version})
    return profile


async def approve_profile(
    session: AsyncSession, profile_id: int, expected_version: int, actor: User
) -> RcsaScoringProfile:
    profile = await lock(session, RcsaScoringProfile, profile_id, code="rcsa.profile_not_found")
    if profile.status != ProfileStatus.DRAFT:
        raise Conflict("这个评分版本已经批准", code="rcsa.profile_immutable")
    check_version(profile, expected_version)
    problems = profile_problems(profile)
    if problems:
        raise Conflict(
            "评分口径还没有补全，不能批准",
            code="rcsa.profile_incomplete",
            params={"problems": problems},
        )
    profile.status = ProfileStatus.APPROVED
    profile.approved_by = actor.id
    profile.approved_at = _now()
    bump(profile, actor)
    await session.flush()
    await audit(session, actor, "profile_approve", "RcsaScoringProfile", profile.id,
                {"key": profile.key, "revision": profile.revision, "version": profile.version})
    return profile


# ── 活动草稿 ────────────────────────────────────────────────


async def counts(session: AsyncSession, campaign_ids: list[int]) -> dict[int, CampaignCounts]:
    result = {campaign_id: CampaignCounts() for campaign_id in campaign_ids}
    if not campaign_ids:
        return result
    rows = await session.execute(
        select(RcsaItem.campaign_id, RcsaItem.status, func.count())
        .where(RcsaItem.campaign_id.in_(campaign_ids))
        .group_by(RcsaItem.campaign_id, RcsaItem.status)
    )
    for campaign_id, status, n in rows:
        tally = result[campaign_id]
        tally.total += n
        if status == ItemStatus.SUBMITTED:
            tally.submitted += n
        elif status == ItemStatus.APPROVED:
            tally.approved += n
        elif status == ItemStatus.EXCLUDED:
            tally.excluded += n
    return result


async def campaign_out(session: AsyncSession, campaign: RcsaCampaign) -> CampaignOut:
    out = CampaignOut.model_validate(campaign)
    out.counts = (await counts(session, [campaign.id]))[campaign.id]
    return out


async def list_campaigns(
    session: AsyncSession, status: CampaignStatus | None, limit: int, offset: int
) -> tuple[list[CampaignOut], int]:
    statement = select(RcsaCampaign)
    if status is not None:
        statement = statement.where(RcsaCampaign.status == status)
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = list(
        await session.scalars(statement.order_by(RcsaCampaign.id.desc()).limit(limit).offset(offset))
    )
    tallies = await counts(session, [row.id for row in rows])
    outs = []
    for row in rows:
        out = CampaignOut.model_validate(row)
        out.counts = tallies[row.id]
        outs.append(out)
    return outs, int(total or 0)


async def _check_owner(session: AsyncSession, owner_id: int) -> None:
    owner = await _person(session, owner_id)
    if not owner.is_active or not has_permission(owner.role, Permission.RCSA_MANAGE):
        raise Conflict("活动负责人需要是有 RCSA 管理权限的在职账号", code="rcsa.owner_lacks_permission")


async def create_campaign(
    session: AsyncSession, payload: CampaignCreateIn, actor: User
) -> RcsaCampaign:
    await fetch(session, Framework, payload.framework_id, code="rcsa.framework_not_found")
    await fetch(session, RcsaScoringProfile, payload.profile_id, code="rcsa.profile_not_found")
    owner_id = payload.owner_id or actor.id
    await _check_owner(session, owner_id)
    if payload.predecessor_campaign_id is not None:
        await fetch(session, RcsaCampaign, payload.predecessor_campaign_id, code="rcsa.campaign_not_found")
    if payload.audit_engagement_id is not None:
        await fetch(session, AuditEngagement, payload.audit_engagement_id, code="rcsa.engagement_not_found")
    campaign = RcsaCampaign(
        **payload.model_dump(exclude={"owner_id"}),
        owner_id=owner_id,
        status=CampaignStatus.DRAFT,
        created_by=actor.id,
        updated_by=actor.id,
    )
    session.add(campaign)
    await session.flush()
    await audit(session, actor, "campaign_create", "RcsaCampaign", campaign.id,
                {"status": "draft", "framework_id": campaign.framework_id, "profile_id": campaign.profile_id})
    return campaign


async def update_campaign(
    session: AsyncSession, campaign_id: int, payload: CampaignUpdateIn, actor: User
) -> RcsaCampaign:
    campaign = await lock(session, RcsaCampaign, campaign_id, code="rcsa.campaign_not_found")
    require_status(campaign, CampaignStatus.DRAFT)
    check_version(campaign, payload.expected_version)
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    if "profile_id" in changes:
        await fetch(session, RcsaScoringProfile, changes["profile_id"], code="rcsa.profile_not_found")
    if "owner_id" in changes:
        await _check_owner(session, changes["owner_id"])
    if changes.get("audit_engagement_id") is not None:
        await fetch(session, AuditEngagement, changes["audit_engagement_id"], code="rcsa.engagement_not_found")
    start = changes.get("period_start", campaign.period_start)
    end = changes.get("period_end", campaign.period_end)
    if start > end:
        raise Conflict("评估期间的结束日早于开始日", code="rcsa.period_order")
    for field, value in changes.items():
        setattr(campaign, field, value)
    bump(campaign, actor)
    await session.flush()
    await audit(session, actor, "campaign_update", "RcsaCampaign", campaign.id,
                {"fields": sorted(changes), "version": campaign.version})
    return campaign


# ── 范围 ────────────────────────────────────────────────────


async def _mapped_controls(session: AsyncSession, framework_id: int) -> dict[int, tuple[Control, list[str]]]:
    """框架里有已确认映射、且未被合并的控制点 → 它映射到的框架项编号（一控制只出现一次）。"""
    rows = await session.execute(
        select(Control, FrameworkItem.code)
        .join(Mapping, Mapping.control_id == Control.id)
        .join(FrameworkItem, FrameworkItem.id == Mapping.framework_item_id)
        .where(FrameworkItem.framework_id == framework_id, Control.status != "merged")
        .order_by(Control.code, FrameworkItem.order_index, FrameworkItem.code)
    )
    found: dict[int, tuple[Control, list[str]]] = {}
    for control, code in rows:
        found.setdefault(control.id, (control, []))[1].append(code)
    return found


async def scope(session: AsyncSession, campaign_id: int) -> list[ScopeRow]:
    campaign = await fetch(session, RcsaCampaign, campaign_id, code="rcsa.campaign_not_found")
    included = set(
        await session.scalars(select(RcsaItem.control_id).where(RcsaItem.campaign_id == campaign.id))
    )
    return [
        ScopeRow(
            control_id=control.id, code=control.code, title=control.title,
            owner_user_id=control.owner_user_id, framework_items=codes,
            included=control.id in included,
        )
        for control, codes in (await _mapped_controls(session, campaign.framework_id)).values()
    ]


async def list_items(session: AsyncSession, campaign_id: int) -> list[RcsaItem]:
    await fetch(session, RcsaCampaign, campaign_id, code="rcsa.campaign_not_found")
    return list(
        await session.scalars(
            select(RcsaItem).where(RcsaItem.campaign_id == campaign_id).order_by(RcsaItem.id)
        )
    )


async def add_items(
    session: AsyncSession, campaign_id: int, payload: ItemsAddIn, actor: User
) -> list[RcsaItem]:
    campaign = await lock(session, RcsaCampaign, campaign_id, code="rcsa.campaign_not_found")
    require_status(campaign, CampaignStatus.DRAFT)
    if payload.assignee_id is not None:
        await _check_assignee(session, payload.assignee_id)
    if payload.reviewer_id is not None:
        await _check_reviewer(session, payload.reviewer_id)
    in_scope = await _mapped_controls(session, campaign.framework_id)
    existing = set(
        await session.scalars(select(RcsaItem.control_id).where(RcsaItem.campaign_id == campaign.id))
    )
    created = []
    for control_id in dict.fromkeys(payload.control_ids):
        if control_id not in in_scope:
            raise Conflict(
                "控制点不在这个框架的已映射范围内",
                code="rcsa.control_not_in_scope",
                params={"control_id": control_id},
            )
        if control_id in existing:
            raise Conflict(
                "这个控制点已经在活动范围里", code="rcsa.item_exists", params={"control_id": control_id}
            )
        control = in_scope[control_id][0]
        # 控制 owner 只是推荐的作答人，不自动获得任何审批权。
        assignee_id = payload.assignee_id or control.owner_user_id
        _distinct(assignee_id, payload.reviewer_id)
        item = RcsaItem(
            campaign_id=campaign.id, control_id=control_id, assignee_id=assignee_id,
            reviewer_id=payload.reviewer_id, status=ItemStatus.PENDING,
            created_by=actor.id, updated_by=actor.id,
        )
        session.add(item)
        created.append(item)
    await session.flush()
    await audit(session, actor, "scope_add", "RcsaCampaign", campaign.id,
                {"item_ids": [item.id for item in created], "control_ids": [i.control_id for i in created]})
    return created


async def remove_item(session: AsyncSession, item_id: int, actor: User) -> None:
    item = await fetch(session, RcsaItem, item_id, code="rcsa.item_not_found")
    campaign = await lock(session, RcsaCampaign, item.campaign_id, code="rcsa.campaign_not_found")
    require_status(campaign, CampaignStatus.DRAFT)
    item = await lock(session, RcsaItem, item_id, code="rcsa.item_not_found")
    await session.delete(item)
    await session.flush()
    await audit(session, actor, "scope_remove", "RcsaCampaign", campaign.id,
                {"item_id": item_id, "control_id": item.control_id})


async def assign(session: AsyncSession, item_id: int, payload: AssignIn, actor: User) -> RcsaItem:
    item = await fetch(session, RcsaItem, item_id, code="rcsa.item_not_found")
    campaign = await lock(session, RcsaCampaign, item.campaign_id, code="rcsa.campaign_not_found", share=True)
    if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.ACTIVE):
        raise Conflict("活动当前状态不允许改派", code="rcsa.campaign_state",
                       params={"status": str(campaign.status)})
    item = await lock(session, RcsaItem, item_id, code="rcsa.item_not_found")
    check_version(item, payload.expected_version)
    if campaign.status == CampaignStatus.ACTIVE and not payload.reason:
        raise Conflict("激活后改派需要写明理由", code="rcsa.reason_required")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version", "reason"})
    if changes.get("assignee_id") is not None:
        await _check_assignee(session, changes["assignee_id"])
    if changes.get("reviewer_id") is not None:
        await _check_reviewer(session, changes["reviewer_id"])
    before = {"assignee_id": item.assignee_id, "reviewer_id": item.reviewer_id}
    assignee_id = changes.get("assignee_id", item.assignee_id)
    reviewer_id = changes.get("reviewer_id", item.reviewer_id)
    _distinct(assignee_id, reviewer_id)
    item.assignee_id, item.reviewer_id = assignee_id, reviewer_id
    bump(item, actor)
    await session.flush()
    await audit(session, actor, "assign", "RcsaItem", item.id,
                {"assignee_id": assignee_id, "reviewer_id": reviewer_id, "reason": payload.reason,
                 "version": item.version}, before=before)
    return item


# ── 激活 ────────────────────────────────────────────────────


def _blocker(code: str, item: RcsaItem | None = None, control: Control | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"code": code}
    if item is not None:
        entry["item_id"] = item.id
    if control is not None:
        entry["control_code"] = control.code
    return entry


async def _activation_blockers(
    session: AsyncSession, campaign: RcsaCampaign, items: list[RcsaItem], profile: RcsaScoringProfile
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not items:
        blockers.append(_blocker(code="rcsa.blocked.scope_empty"))
    if profile.status != ProfileStatus.APPROVED:
        blockers.append(_blocker(code="rcsa.blocked.profile_not_approved"))
    owner = await session.get(User, campaign.owner_id)
    if owner is None or not owner.is_active or not has_permission(owner.role, Permission.RCSA_MANAGE):
        blockers.append(_blocker(code="rcsa.blocked.owner_unavailable"))
    mapped = await _mapped_controls(session, campaign.framework_id)
    for item in items:
        control = await session.get(Control, item.control_id)
        if control.status == "merged":
            blockers.append(_blocker(code="rcsa.blocked.control_merged", item=item, control=control))
        elif item.control_id not in mapped:
            blockers.append(_blocker(code="rcsa.blocked.control_unmapped", item=item, control=control))
        if item.provider_type != ProviderType.INTERNAL:
            blockers.append(_blocker(code="rcsa.blocked.provider_not_internal", item=item, control=control))
        # 每个分支各写一条字面量 code：i18n 检查静态扫描，拼出来的它看不见。
        assignee = await session.get(User, item.assignee_id) if item.assignee_id else None
        if assignee is None:
            blockers.append(_blocker(code="rcsa.blocked.assignee_missing", item=item, control=control))
        elif not assignee.is_active:
            blockers.append(_blocker(code="rcsa.blocked.assignee_inactive", item=item, control=control))
        elif not _can(assignee, Permission.RCSA_RESPOND):
            blockers.append(_blocker(code="rcsa.blocked.assignee_lacks_permission", item=item, control=control))
        reviewer = await session.get(User, item.reviewer_id) if item.reviewer_id else None
        if reviewer is None:
            blockers.append(_blocker(code="rcsa.blocked.reviewer_missing", item=item, control=control))
        elif not reviewer.is_active:
            blockers.append(_blocker(code="rcsa.blocked.reviewer_inactive", item=item, control=control))
        elif not _can(reviewer, Permission.RCSA_REVIEW):
            blockers.append(_blocker(code="rcsa.blocked.reviewer_lacks_permission", item=item, control=control))
    return blockers


async def _freeze_item(session: AsyncSession, item: RcsaItem, framework_id: int) -> None:
    control = await session.get(Control, item.control_id)
    item.control_snapshot = {
        "id": control.id, "code": control.code, "title": control.title,
        "statement": control.statement, "category": control.category,
        "owner_user_id": control.owner_user_id,
    }
    mappings = await session.execute(
        select(Mapping, FrameworkItem)
        .join(FrameworkItem, FrameworkItem.id == Mapping.framework_item_id)
        .where(Mapping.control_id == control.id, FrameworkItem.framework_id == framework_id)
        .order_by(FrameworkItem.order_index, FrameworkItem.code)
    )
    item.mapping_snapshot = [
        {"framework_item_id": fi.id, "code": fi.code, "title": fi.title, "strength": str(m.strength)}
        for m, fi in mappings
    ]
    sources = await session.execute(
        select(ControlSource, Clause)
        .join(Clause, Clause.id == ControlSource.clause_id)
        .where(ControlSource.control_id == control.id)
        .order_by(ControlSource.id)
    )
    item.source_snapshot = [
        {"clause_id": clause.id, "document_id": clause.document_id,
         "citation_label": clause.citation_label, "relation": str(source.relation)}
        for source, clause in sources
    ]


async def activate(
    session: AsyncSession, campaign_id: int, expected_version: int, actor: User, client_key: str
) -> RcsaCampaign:
    campaign = await lock(session, RcsaCampaign, campaign_id, code="rcsa.campaign_not_found")
    command = Command(actor, "activate", "RcsaCampaign", campaign.id, client_key,
                      {"expected_version": expected_version})
    if await command.replay(session) is not None:
        return campaign
    require_owner(campaign, actor)
    require_status(campaign, CampaignStatus.DRAFT)
    check_version(campaign, expected_version)
    items = list(
        await session.scalars(
            select(RcsaItem).where(RcsaItem.campaign_id == campaign.id).order_by(RcsaItem.id).with_for_update()
        )
    )
    profile = await session.get(RcsaScoringProfile, campaign.profile_id)
    blockers = await _activation_blockers(session, campaign, items, profile)
    if blockers:
        raise Conflict("活动还不能激活", code="rcsa.activation_blocked", params={"blockers": blockers})

    framework = await session.get(Framework, campaign.framework_id)
    campaign.framework_snapshot = {
        "id": framework.id, "key": framework.key, "name": framework.name_en, "version": framework.version,
    }
    campaign.profile_snapshot = {
        "id": profile.id, "key": profile.key, "revision": profile.revision,
        "algorithm": str(profile.algorithm), "definitions": profile.definitions, "bands": profile.bands,
        "horizon": profile.horizon, "approved_by": profile.approved_by,
        "approved_at": profile.approved_at.isoformat() if profile.approved_at else None,
    }
    for item in items:
        await _freeze_item(session, item, framework.id)
        item.status = ItemStatus.PENDING
        bump(item, actor)
    campaign.status = CampaignStatus.ACTIVE
    campaign.activated_by = actor.id
    campaign.activated_at = _now()
    bump(campaign, actor)
    await session.flush()
    await command.done(session, campaign.id, {"status": "active", "version": campaign.version,
                                              "items": len(items), "profile_id": profile.id})
    return campaign
