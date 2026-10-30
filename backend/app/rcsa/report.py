"""活动报告（基本版；完整统计与跨期规则在 PR-5）。

已关闭的活动一律以 `closed_snapshot` 为准；未关闭的现算同样结构的数据并标 Draft，已取消
的标 Cancelled，都不能当作完成活动引用。行动与接受到期的**当前**状态另列在
`current_follow_up`，不覆盖闭期结论。

几条口径：
- 进度分母是冻结的评估项数，排除单列；不同指标不合并成「合规率」；
- 情景按评分档位计数，叫「情景」不叫「风险」——同一风险在多个控制下会重复出现，
  不能当作机构风险总数；
- 报告不带证据位置、测试人或回答正文，打印出去也不会带出服务器路径或内部链接；
- 跨期只并列、不算改善百分比，也不平均序数分；同一控制、同一情景键、同一评分版本与时域
  才算可比，否则照样并列但标「不可直接比较」。上一期必须已关闭才能当基线；
- 汇总用档位计数和最高档位，不给「平均风险」。
"""

from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.rcsa.access import fetch
from app.rcsa.ai import CHANNELS, channel_settings
from app.rcsa.closing import item_snapshots
from app.rcsa.consistency import contradictions
from app.rcsa.models import (
    ActionStatus,
    CampaignStatus,
    DispositionStatus,
    RcsaAction,
    RcsaCampaign,
    RcsaItem,
    RcsaItemRisk,
)
from app.rcsa.profiles import band_for
from app.rcsa.schemas import AnswerV1

_OPEN = {ActionStatus.OPEN, ActionStatus.IN_PROGRESS, ActionStatus.PENDING_VERIFICATION}


async def _items(session: AsyncSession, campaign: RcsaCampaign) -> list[dict[str, Any]]:
    if campaign.closed_snapshot is not None:
        return campaign.closed_snapshot["items"]
    rows = list(await session.scalars(
        select(RcsaItem).where(RcsaItem.campaign_id == campaign.id).order_by(RcsaItem.id)
    ))
    return [entry for entry, _ in await item_snapshots(session, rows)]


def _band(bands: list[dict[str, Any]], score: int | None) -> str:
    if score is None:
        return "Unrated"
    try:
        return band_for(bands, score)
    except ValueError:
        return "Unbanded"


def _target(snapshot: dict[str, Any] | None) -> str | None:
    if not snapshot:
        return None
    asset = snapshot.get("asset")
    return f"{asset['name']} · {asset['environment']}" if asset else "Process implementation"


async def build(session: AsyncSession, campaign_id: int) -> dict[str, Any]:
    campaign = await fetch(session, RcsaCampaign, campaign_id, code="rcsa.campaign_not_found")
    today = datetime.now(UTC).date()
    items = await _items(session, campaign)
    profile = campaign.profile_snapshot or {}
    bands = profile.get("bands") or []

    statuses = Counter(item["status"] for item in items)
    tests: Counter[str] = Counter()
    inherent: Counter[str] = Counter()
    residual: Counter[str] = Counter()
    dispositions: Counter[str] = Counter()
    actions: Counter[str] = Counter({"open": 0, "overdue": 0, "verified": 0, "cancelled": 0})
    not_assessed, limitations, acceptances, rows = [], [], [], []
    signal_reviews = []
    for item in items:
        code = (item.get("control") or {}).get("code")
        target = _target(item.get("implementation"))
        answer = (item.get("response") or {}).get("answer") or {}
        if item.get("response"):
            tests[answer.get("independent_test_status") or "unanswered"] += 1
            gaps = [name for name in ("design", "operating") if (answer.get(name) or {}).get("rating") == "not_assessed"]
            if gaps:
                not_assessed.append({
                    "control_code": code, "target_label": target, "ratings": gaps, "reason": answer.get("not_assessed_reason", ""),
                    "follow_up_due": (answer.get("evidence_followup") or {}).get("due_at"),
                })
            if answer.get("limitations"):
                limitations.append({"control_code": code, "target_label": target, "text": answer["limitations"]})
        if answer.get("signals"):
            response = item["response"]
            flagged = contradictions(
                AnswerV1.model_validate(answer), campaign.period_start, campaign.period_end,
                has_risks=bool(response.get("risk_snapshot")),
            )
            signal_reviews.append({
                "item_id": item["id"], "control_code": code, "target_label": target, "signals": len(answer["signals"]),
                "contradictions": len(flagged),
                "approval_rationale_recorded": any(
                    review["decision"] == "approve" and review["target_version"] == response["revision"]
                    and bool(review["reason"].strip()) for review in item.get("reviews", [])
                ),
            })
        scenario_rows = []
        for risk in item.get("risks", []):
            inherent[_band(bands, risk["inherent_score"])] += 1
            residual[_band(bands, risk["residual_score"])] += 1
            dispositions[risk["disposition"] or "none"] += 1
            if risk["disposition"] == "accept" and risk["disposition_status"] == DispositionStatus.APPROVED:
                acceptances.append({
                    "control_code": code, "target_label": target, "scenario_key": risk["scenario_key"], "accept_until": risk["accept_until"],
                    "expired": date.fromisoformat(risk["accept_until"]) < today,
                })
            for action in risk.get("actions", []):
                if action["status"] in _OPEN:
                    actions["open"] += 1
                    actions["overdue"] += date.fromisoformat(action["due_at"]) < today
                elif action["status"] in (ActionStatus.VERIFIED, ActionStatus.CANCELLED):
                    actions[action["status"]] += 1
            scenario_rows.append({
                "scenario_key": risk["scenario_key"], "event": risk["event"],
                "inherent_score": risk["inherent_score"], "inherent_band": _band(bands, risk["inherent_score"]),
                "residual_score": risk["residual_score"], "residual_band": _band(bands, risk["residual_score"]),
                "disposition": risk["disposition"], "disposition_status": risk["disposition_status"],
            })
        rows.append({
            "item_id": item["id"], "control_code": code, "target_label": target, "control_title": (item.get("control") or {}).get("title"),
            "status": item["status"],
            "design": (answer.get("design") or {}).get("rating"),
            "operating": (answer.get("operating") or {}).get("rating"),
            "independent_test_status": answer.get("independent_test_status"),
            "scenarios": scenario_rows,
        })

    ai_on = False
    for channel in CHANNELS:
        ai_on = ai_on or (await channel_settings(session, channel))["enabled"]
    return {
        "watermark": {CampaignStatus.CLOSED: None, CampaignStatus.CANCELLED: "cancelled"}.get(
            campaign.status, "draft"
        ),
        "source": "closed_snapshot" if campaign.closed_snapshot is not None else "live",
        "generated_at": datetime.now(UTC).isoformat(),
        "campaign": {
            "id": campaign.id, "name": campaign.name, "status": str(campaign.status),
            "period_start": campaign.period_start.isoformat(), "period_end": campaign.period_end.isoformat(),
            "due_at": campaign.due_at.isoformat(), "owner_id": campaign.owner_id,
            "closed_at": campaign.closed_at.isoformat() if campaign.closed_at else None,
            "framework": campaign.framework_snapshot,
            "profile": {key: profile.get(key) for key in ("key", "revision", "algorithm", "horizon", "bands")},
        },
        "scope": {
            "total": len(items), "approved": statuses["approved"], "excluded": statuses["excluded"],
            "submitted": statuses["submitted"],
            "in_progress": statuses["pending"] + statuses["draft"] + statuses["returned"],
        },
        "exclusions": [
            {"control_code": (item.get("control") or {}).get("code"),
             "target_label": _target(item.get("implementation")), "reason": item["exclusion"]["reason"]}
            for item in items if item.get("exclusion")
        ],
        "not_assessed": not_assessed,
        "independent_tests": {key: tests[key] for key in ("provided", "not_provided", "not_applicable")}
        | ({"unanswered": tests["unanswered"]} if tests["unanswered"] else {}),
        "scenario_bands": {"inherent": dict(inherent), "residual": dict(residual)},
        "dispositions": dict(dispositions),
        "acceptances": acceptances,
        "actions": dict(actions),
        "limitations": limitations,
        "signal_reviews": signal_reviews,
        "acknowledgements": (campaign.closed_snapshot or {}).get("acknowledgements"),
        "highest_residual_band": _band(bands, max(scores)) if (scores := [
            risk["residual_score"] for item in items for risk in item.get("risks", [])
            if risk["residual_score"] is not None
        ]) else None,
        # 报告本身从不带模型生成的内容；启用时回答里可能有作答人采纳过的建议（逐条有审计）。
        "ai_assistance": "enabled" if ai_on else "not_enabled",
        "comparison": await _comparison(session, campaign, items),
        "items": rows,
        "current_follow_up": await _current_follow_up(session, campaign.id, today),
    }


async def _current_follow_up(session: AsyncSession, campaign_id: int, today: date) -> dict[str, Any]:
    """行动与接受的当前状态：闭期后仍在变化，单独列出，不改闭期结论。"""
    rows = await session.execute(
        select(RcsaAction, RcsaItemRisk, RcsaItem)
        .join(RcsaItemRisk, RcsaItemRisk.id == RcsaAction.item_risk_id)
        .join(RcsaItem, RcsaItem.id == RcsaItemRisk.item_id)
        .where(RcsaItem.campaign_id == campaign_id)
        .order_by(RcsaAction.id)
    )
    actions = [{
        "id": action.id, "title": action.title, "control_code": (item.control_snapshot or {}).get("code"),
        "target_label": _target(item.implementation_snapshot),
        "status": str(action.status), "due_at": action.due_at.isoformat(),
        "overdue": action.status in _OPEN and action.due_at < today,
    } for action, _risk, item in rows]
    risks = await session.execute(
        select(RcsaItemRisk, RcsaItem)
        .join(RcsaItem, RcsaItem.id == RcsaItemRisk.item_id)
        .where(RcsaItem.campaign_id == campaign_id, RcsaItemRisk.disposition == "accept",
               RcsaItemRisk.disposition_status == DispositionStatus.APPROVED)
        .order_by(RcsaItemRisk.id)
    )
    acceptances = [{
        "risk_id": risk.id, "control_code": (item.control_snapshot or {}).get("code"),
        "target_label": _target(item.implementation_snapshot),
        "accept_until": risk.accept_until.isoformat(), "expired": risk.accept_until < today,
    } for risk, item in risks]
    return {"actions": actions, "acceptances": acceptances}


def _scenarios(items: list[dict[str, Any]]) -> dict[tuple[int, int | None, str], tuple[str, dict[str, Any], str | None]]:
    """以控制、实施记录、情景键匹配；旧快照缺实施记录时仍按整项控制比较。"""
    found = {}
    for item in items:
        control = item.get("control") or {}
        for risk in item.get("risks", []):
            found[(control.get("id"), item.get("implementation_id"), risk["scenario_key"])] = (control.get("code"), risk, _target(item.get("implementation")))
    return found


async def _comparison(session: AsyncSession, campaign: RcsaCampaign, items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if campaign.predecessor_campaign_id is None:
        return None
    previous = await fetch(session, RcsaCampaign, campaign.predecessor_campaign_id, code="rcsa.campaign_not_found")
    result: dict[str, Any] = {
        "previous": {"id": previous.id, "name": previous.name, "status": str(previous.status)},
        "comparable": False, "reason": None, "scenarios": [],
    }
    if previous.closed_snapshot is None:
        return result | {"reason": "previous_not_closed"}
    before, now = previous.profile_snapshot or {}, campaign.profile_snapshot or {}
    same = all(before.get(key) == now.get(key) for key in ("key", "revision", "horizon"))
    previous_scenarios = _scenarios(previous.closed_snapshot["items"])
    current_scenarios = _scenarios(items)
    rows = []
    for key in sorted(previous_scenarios.keys() | current_scenarios.keys(), key=lambda k: (str(k[0]), str(k[1]), k[2])):
        old = previous_scenarios.get(key)
        new = current_scenarios.get(key)
        if old is None:
            status = "new"
        elif new is None:
            status = "removed"
        else:
            status = "comparable" if same else "not_directly_comparable"
        old_score = old[1]["residual_score"] if old else None
        new_score = new[1]["residual_score"] if new else None
        rows.append({
            "control_code": (new or old)[0], "target_label": (new or old)[2], "implementation_id": key[1], "scenario_key": key[2], "status": status,
            "previous_residual_score": old_score,
            "previous_residual_band": _band(before.get("bands") or [], old_score) if old else None,
            "current_residual_score": new_score,
            "current_residual_band": _band(now.get("bands") or [], new_score) if new else None,
        })
    return result | {"comparable": same, "reason": None if same else "profile_changed", "scenarios": rows}
