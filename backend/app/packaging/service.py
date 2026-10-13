"""按框架把审计准备包装成 zip。"""

import io
import zipfile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conflicts.models import PolicyConflict
from app.controls.models import Control
from app.errors import NotFound
from app.evidence.service import list_items
from app.frameworks.coverage import gaps as framework_gaps
from app.frameworks.coverage import summarize as framework_coverage
from app.frameworks.coverage import superseded_support
from app.frameworks.models import Framework, FrameworkItem, Mapping
from app.maturity.aggregation import build_summary
from app.maturity.models import MaturityAssessment
from app.maturity.schemas import MaturitySummaryOut
from app.packaging.docx import build_readiness_docx
from app.packaging.xlsx import build_evidence_xlsx, build_mappings_xlsx


async def conflict_rows(session: AsyncSession) -> list[PolicyConflict]:
    """只取已确认的冲突。

    待确认的活在 proposals 里，本函数一行都不碰它们——准备包是拿去给审计看的。
    这也是 PolicyConflict 不设 status 字段的好处：表里有的就是数得进准备包的。
    """
    return list(
        await session.scalars(select(PolicyConflict).order_by(PolicyConflict.id))
    )


async def maturity_summary(
    session: AsyncSession, framework_id: int
) -> MaturitySummaryOut | None:
    """按框架取最新一次评估的汇总；没有评估时返回 None，不 500。"""
    assessment = await session.scalar(
        select(MaturityAssessment)
        .where(MaturityAssessment.framework_id == framework_id)
        .order_by(MaturityAssessment.as_of_date.desc(), MaturityAssessment.id.desc())
        .limit(1)
    )
    if assessment is None:
        return None
    return await build_summary(session, assessment)


async def evidence_with_freshness(session: AsyncSession):
    return await list_items(session, limit=10_000)


async def mapping_rows(session: AsyncSession, framework_id: int) -> list[list]:
    """控制点↔框架项明细。覆盖度汇总没有控制点代码/强度/理由，不能拿来填这张表。"""
    rows = await session.execute(
        select(Mapping, Control, FrameworkItem)
        .join(Control, Control.id == Mapping.control_id)
        .join(FrameworkItem, FrameworkItem.id == Mapping.framework_item_id)
        .where(FrameworkItem.framework_id == framework_id, Control.status != "merged")
        .order_by(Control.code, FrameworkItem.order_index, Mapping.id)
    )
    return [
        [
            control.code,
            control.title,
            item.code,
            item.title,
            mapping.strength.value,
            mapping.confidence,
            mapping.rationale,
        ]
        for mapping, control, item in rows
    ]


async def build_package(session: AsyncSession, framework_id: int, *, language: str) -> bytes:
    framework = await session.get(Framework, framework_id)
    if framework is None:
        raise NotFound("框架不存在", code="framework.not_found")
    # 覆盖度 / 差距 / 成熟度 / 证据全部走既有取数函数，不重写查询
    coverage = await framework_coverage(session, framework_id)
    gaps = await framework_gaps(session, framework_id)
    maturity = await maturity_summary(session, framework_id)
    evidence = await evidence_with_freshness(session)
    conflicts = await conflict_rows(session)

    readiness = build_readiness_docx(
        framework=framework, coverage=coverage, gaps=gaps,
        maturity=maturity, conflicts=conflicts, language=language,
        superseded=await superseded_support(session, framework_id),
    )
    mappings = build_mappings_xlsx(await mapping_rows(session, framework_id))
    evidence_sheet = build_evidence_xlsx(evidence)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("readiness.docx", readiness)
        bundle.writestr("mappings.xlsx", mappings)
        bundle.writestr("evidence.xlsx", evidence_sheet)
    return buffer.getvalue()
