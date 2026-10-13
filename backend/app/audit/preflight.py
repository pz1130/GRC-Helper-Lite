"""按证据算出一个审计项目的准备度预览。"""

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEngagement
from app.environment.models import Implementation, ImplementationStatus, TechAsset
from app.errors import AppError
from app.evidence.freshness import display_status
from app.evidence.models import EvidenceItem
from app.frameworks.models import FrameworkItem, Mapping


async def build_preflight(
    session: AsyncSession, engagement: AuditEngagement, *, language: str
) -> dict:
    if engagement.framework_id is None:
        raise AppError("请先为审计项目选择评估框架", code="audit.framework_required")
    items = list(
        await session.scalars(
            select(FrameworkItem)
            .where(FrameworkItem.framework_id == engagement.framework_id)
            .order_by(FrameworkItem.order_index, FrameworkItem.id)
        )
    )
    parent_ids = {item.parent_id for item in items if item.parent_id is not None}
    leaves = [item for item in items if item.id not in parent_ids]
    item_ids = {item.id for item in leaves}
    mappings = list(
        await session.scalars(select(Mapping).where(Mapping.framework_item_id.in_(item_ids)))
    )
    controls_by_item: dict[int, set[int]] = defaultdict(set)
    for mapping in mappings:
        controls_by_item[mapping.framework_item_id].add(mapping.control_id)
    control_ids = {control_id for values in controls_by_item.values() for control_id in values}
    implementations = list(
        await session.scalars(
            select(Implementation).where(
                Implementation.control_id.in_(control_ids),
                Implementation.status != ImplementationStatus.NOT_APPLICABLE,
            )
        )
    )
    evidence = list(
        await session.scalars(select(EvidenceItem).where(EvidenceItem.control_id.in_(control_ids)))
    )
    assets = {
        asset.id: asset.name
        for asset in await session.scalars(
            select(TechAsset).where(
                TechAsset.id.in_(
                    {row.tech_asset_id for row in implementations if row.tech_asset_id}
                )
            )
        )
    }
    impl_by_control: dict[int, list[Implementation]] = defaultdict(list)
    evidence_by_control: dict[int, list[EvidenceItem]] = defaultdict(list)
    for row in implementations:
        impl_by_control[row.control_id].append(row)
    for row in evidence:
        evidence_by_control[row.control_id].append(row)
    now = datetime.now(UTC)
    rows = []
    summary = {"green": 0, "yellow": 0, "red": 0}
    for item in leaves:
        mapped_controls = controls_by_item[item.id]
        item_impl = [row for control in mapped_controls for row in impl_by_control[control]]
        item_evidence = [row for control in mapped_controls for row in evidence_by_control[control]]
        valid = [
            row
            for row in item_evidence
            if display_status(row.status, row.valid_until, now) == "valid"
        ]
        expired = [
            row
            for row in item_evidence
            if display_status(row.status, row.valid_until, now) == "expired"
        ]
        if valid:
            readiness = "green"
            reason = "已有有效证据" if language == "zh" else "Valid evidence is available"
        elif item_impl:
            readiness = "yellow"
            reason = (
                "已有落地实现，但缺少有效证据"
                if language == "zh"
                else "Implementation exists, but valid evidence is missing"
            )
        else:
            readiness = "red"
            reason = (
                "未登记落地实现或有效证据"
                if language == "zh"
                else "No implementation or valid evidence is registered"
            )
        summary[readiness] += 1
        subject = f"{item.code} {item.title}".strip()
        likely_question = (
            f"请说明贵组织如何满足 {subject}，并提供支持证据。"
            if language == "zh"
            else f"How does the organization meet {subject}, and what evidence supports it?"
        )
        rows.append(
            {
                "framework_item_id": item.id,
                "code": item.code,
                "title": item.title,
                "likely_question": likely_question,
                "readiness": readiness,
                "reason": reason,
                "control_count": len(mapped_controls),
                "implementation_count": len(item_impl),
                "evidence_count": len(item_evidence),
                "valid_evidence_count": len(valid),
                "expired_evidence_count": len(expired),
                "evidence_titles": [row.title for row in item_evidence],
                "tool_names": sorted(
                    {assets[row.tech_asset_id] for row in item_impl if row.tech_asset_id in assets}
                ),
            }
        )
    return {"engagement_id": engagement.id, "summary": summary, "rows": rows}
