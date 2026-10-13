from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.frameworks.models import FrameworkItem
from app.maturity.models import MaturityAssessment, MaturityScore
from app.maturity.schemas import (
    MaturityAggregateOut,
    MaturityGroupOut,
    MaturityItemOut,
    MaturitySummaryOut,
)


def _average(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _aggregate(items: list[FrameworkItem], scores: dict[int, MaturityScore]) -> MaturityAggregateOut:
    rows = [scores[item.id] for item in items if item.id in scores]
    return MaturityAggregateOut(
        total_items=len(items),
        scored_items=len(rows),
        doc_average=_average([row.doc_score for row in rows]),
        impl_average=_average([row.impl_score for row in rows]),
    )


async def build_summary(
    session: AsyncSession, assessment: MaturityAssessment
) -> MaturitySummaryOut:
    framework_items = list(
        await session.scalars(
            select(FrameworkItem)
            .where(FrameworkItem.framework_id == assessment.framework_id)
            .order_by(FrameworkItem.order_index, FrameworkItem.id)
        )
    )
    score_rows = list(
        await session.scalars(
            select(MaturityScore).where(MaturityScore.assessment_id == assessment.id)
        )
    )
    scores = {row.framework_item_id: row for row in score_rows}
    item_by_id = {item.id: item for item in framework_items}
    parent_ids = {item.parent_id for item in framework_items if item.parent_id is not None}
    leaves = [item for item in framework_items if item.id not in parent_ids]

    def root_for(item: FrameworkItem) -> FrameworkItem:
        current = item
        seen: set[int] = set()
        while current.parent_id in item_by_id and current.id not in seen:
            seen.add(current.id)
            current = item_by_id[current.parent_id]
        return current

    grouped: dict[int, list[FrameworkItem]] = defaultdict(list)
    roots: dict[int, FrameworkItem] = {}
    for leaf in leaves:
        root = root_for(leaf)
        roots[root.id] = root
        grouped[root.id].append(leaf)

    groups: list[MaturityGroupOut] = []
    for root_id, group_items in grouped.items():
        aggregate = _aggregate(group_items, scores)
        groups.append(
            MaturityGroupOut(
                framework_item_id=root_id,
                code=roots[root_id].code,
                title=roots[root_id].title,
                **aggregate.model_dump(),
            )
        )

    details = []
    for item in leaves:
        root = root_for(item)
        score = scores.get(item.id)
        details.append(
            MaturityItemOut(
                framework_item_id=item.id,
                parent_id=item.parent_id,
                group_id=root.id,
                code=item.code,
                title=item.title,
                doc_score=score.doc_score if score else None,
                impl_score=score.impl_score if score else None,
                doc_rationale=score.doc_rationale if score else "",
                impl_rationale=score.impl_rationale if score else "",
                doc_score_source=score.doc_score_source if score else None,
                impl_score_source=score.impl_score_source if score else None,
            )
        )

    return MaturitySummaryOut(
        assessment_id=assessment.id,
        framework_id=assessment.framework_id,
        overall=_aggregate(leaves, scores),
        groups=groups,
        items=details,
    )
