"""语料盲区：你自己的制度里，有多少要求还没变成控制点。

这和框架覆盖度是**两个方向**的问题。框架覆盖度问「外部要求有没有被我们满足」，
它的分母是 NIST 那些框架项；这里问「我们自己写下的要求有没有被系统看见」，
分母是你上传的文档。

两种盲区都是静默的——不做这个扫描，界面上没有任何地方会告诉你：

- **文档传了却从没抽取过**。实测有过：6 份文档里 2 份从未产生任何抽取提案，
  而 `audit_log` 里连一条 `extraction.enqueue` 都没有（当时是直接调函数跑的，
  跑了 4 份漏了 2 份）。那 2 份里的威胁情报要求在控制点库里完全不存在。
- **条款写了规范性要求却没产出控制点**。同一批语料里 58 条规范性条款有 23 条如此。

**规范性判定是启发式**：正则匹配 shall / must / should / is required to，外加几种
不带情态词的要求写法（见 `NORMATIVE`）。它会误报（"This document shall apply to…"
是范围声明不是要求），也会漏报（祈使句、陈述句写的做法，如 "Organisations
restore data…"；表格用行列表达义务）。接口和界面都必须如实标成启发式，
不能让人当成结论。

2026-09-30 在公开语料上实测（KNOWN_ISSUES K18）：只认情态词时 173 条要求漏 33 条，
加上这几种写法后漏 26 条。剩下的漏报没有可匹配的关键词，正则补不上。
"""

import re
from dataclasses import dataclass

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.controls.models import ControlSource
from app.ingest.models import DocStatus, Document
from app.review.models import Proposal, ProposalKind

# Postgres 正则（`~*`，\m / \M 是词边界）。后半截的几种写法是在一批开发样本上逐个
# 量过、几乎不带误报才收的；requires / needs to / to ensure / committed to 误报多于
# 命中，没收。改这里之前先在新样本上量，别照着手上这批调。
NORMATIVE = (
    r"\m(shall|must|should|is required to|are required to)\M"
    r"|\m(expect|expects|expected)\M"
    r"|\m(responsible|accountable) for\M"
    r"|\mresponsibilit(y|ies) (of|for)\M"
    r"|\m(recommend|recommends|recommended|encouraged to)\M"
    r"|\mmay not\M"
    r"|\mprinciple \d+\s*:"
    r"|\m(is|are|be) required\M"
)
# 强制 vs 建议：shall/must/may not/is required 是义务，should、expects、
# recommended 是期望与建议。分开报，因为漏一条义务和漏一条建议不是一回事。
_STRONG = re.compile(r"\b(shall|must|may not|(is|are|be) required)\b", re.IGNORECASE)
MIN_CLAUSE_CHARS = 40


@dataclass(frozen=True)
class DocumentCoverage:
    document_id: int
    title: str
    status: str
    clauses: int
    normative_clauses: int
    controls: int
    proposals: int
    uncovered_normative: int

    @property
    def never_extracted(self) -> bool:
        """一条抽取提案都没产生过——不是「抽了但都被拒」，是根本没跑过。"""
        return self.proposals == 0


def _normative(column) -> object:
    return column.op("~*")(NORMATIVE)


async def document_coverage(session: AsyncSession) -> list[DocumentCoverage]:
    clause_stats = (
        select(
            Clause.document_id.label("document_id"),
            func.count().label("clauses"),
            func.count().filter(
                _normative(Clause.text), func.length(Clause.text) > MIN_CLAUSE_CHARS
            ).label("normative"),
            func.count(distinct(Clause.id)).filter(
                _normative(Clause.text),
                func.length(Clause.text) > MIN_CLAUSE_CHARS,
                Clause.id.notin_(select(ControlSource.clause_id)),
            ).label("uncovered"),
        )
        .group_by(Clause.document_id)
        .subquery()
    )
    control_counts = (
        select(
            Clause.document_id.label("document_id"),
            func.count(distinct(ControlSource.control_id)).label("controls"),
        )
        .join(ControlSource, ControlSource.clause_id == Clause.id)
        .group_by(Clause.document_id)
        .subquery()
    )
    proposal_counts = (
        select(
            Proposal.document_id.label("document_id"),
            func.count().label("proposals"),
        )
        .where(Proposal.kind == ProposalKind.CONTROL_EXTRACT)
        .group_by(Proposal.document_id)
        .subquery()
    )

    rows = await session.execute(
        select(
            Document.id, Document.title, Document.status,
            func.coalesce(clause_stats.c.clauses, 0),
            func.coalesce(clause_stats.c.normative, 0),
            func.coalesce(control_counts.c.controls, 0),
            func.coalesce(proposal_counts.c.proposals, 0),
            func.coalesce(clause_stats.c.uncovered, 0),
        )
        .outerjoin(clause_stats, clause_stats.c.document_id == Document.id)
        .outerjoin(control_counts, control_counts.c.document_id == Document.id)
        .outerjoin(proposal_counts, proposal_counts.c.document_id == Document.id)
        # 已被新版替换的不再是现行要求，它的条款没有控制点不算盲区。
        .where(Document.status != DocStatus.SUPERSEDED)
        .order_by(Document.id)
    )
    return [
        DocumentCoverage(
            document_id=did, title=title,
            status=status.value if hasattr(status, "value") else str(status),
            clauses=clauses, normative_clauses=normative, controls=controls,
            proposals=proposals, uncovered_normative=uncovered,
        )
        for did, title, status, clauses, normative, controls, proposals, uncovered in rows
    ]


@dataclass(frozen=True)
class UncoveredClause:
    clause_id: int
    citation_label: str | None
    heading_path: str | None
    text: str
    strong: bool          # 含义务性措辞（shall/must/may not/is required），而非仅建议


async def uncovered_clauses(
    session: AsyncSession, document_id: int
) -> list[UncoveredClause]:
    """该文档里写了规范性要求、却没有任何控制点引用的条款。"""
    rows = await session.scalars(
        select(Clause)
        .where(
            Clause.document_id == document_id,
            _normative(Clause.text),
            func.length(Clause.text) > MIN_CLAUSE_CHARS,
            Clause.id.notin_(select(ControlSource.clause_id)),
        )
        .order_by(Clause.order_index)
    )
    return [
        UncoveredClause(
            clause_id=c.id, citation_label=c.citation_label, heading_path=c.heading_path,
            text=c.text,
            strong=bool(_STRONG.search(c.text or "")),
        )
        for c in rows
    ]
