from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.controls.models import Control, ControlSource
from app.db import get_session
from app.extraction.drift import upgraded_from
from app.frameworks.coverage import CLOSING
from app.frameworks.models import Framework, FrameworkItem, Mapping, MappingStrength
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.ingest.models import Document
from app.review import service
from app.review.materialize import normalise_statement, sql_normalised_statement
from app.review.models import Proposal, ProposalKind, ProposalStatus
from app.review.schemas import AutoProcessIn, BulkAcceptIn, DecideIn, ProposalOut
from app.review.thresholds import Thresholds, load
from app.worker import enqueue

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


def _clause_ids(proposals: list[Proposal]) -> set[int]:
    return {
        citation["clause_id"]
        for proposal in proposals
        for citation in (proposal.citations or [])
        if isinstance(citation, dict) and type(citation.get("clause_id")) is int
    }


async def clause_context(
    session: AsyncSession, proposals: list[Proposal]
) -> dict[int, dict[str, Any]]:
    """整页一次查完条款来源，不按提案逐条查。

    citations 存的是模型原始产出，只有 {clause_id, quote}；审核人拿到裸数字
    既看不出出自哪份规章，也无从判断引文有没有被断章取义。
    """
    wanted = _clause_ids(proposals)
    if not wanted:
        return {}
    rows = await session.execute(
        select(Clause, Document)
        .join(Document, Document.id == Clause.document_id)
        .where(Clause.id.in_(wanted))
    )
    return {
        clause.id: {
            "document_id": document.id,
            "document_title": document.title,
            "citation_label": clause.citation_label,
            "heading_path": clause.heading_path,
            # 正文不进 citations 输出（太长），只用来判「结论是否强于原文」。
            "_text": clause.text,
        }
        for clause, document in rows
    }


@dataclass(frozen=True)
class PageContext:
    """一页提案渲染所需的全部旁路数据，一次性查好。

    此前每条提案要单独查 3 次（OCR 标记 + 框架项 + 控制点），412 条映射提案
    就是 1200 多次往返。这里按页批量查，往返数与提案条数无关。
    """

    clauses: dict[int, dict[str, Any]] = field(default_factory=dict)
    items: dict[int, FrameworkItem] = field(default_factory=dict)
    controls: dict[int, Control] = field(default_factory=dict)
    ocr_flagged: frozenset[int] = frozenset()
    # 框架项 → 已确认映射 [(控制点编号, 强度)]。审 supporting 时最要紧的一条
    # 上下文：目标项若已被 full/partial 关掉，这条确认了也不会改变任何结论。
    item_mappings: dict[int, list[tuple[str, str]]] = field(default_factory=dict)
    # 控制点 → 它出自哪几条内部条款。控制点是抽取产物，审核者要能回到原文：
    # OQ-5 记着有 28 条已接受的控制点建立在残缺文本上，看不到出处就发现不了。
    control_sources: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    # 提案 id → 正文与它逐字相同的已有控制点。摆给审核者看，不替他们并（OQ-8）。
    statement_duplicates: dict[int, Control] = field(default_factory=dict)
    extraction_reviews: dict[int, dict] = field(default_factory=dict)


def _int(value: Any) -> int | None:
    return value if type(value) is int else None


async def page_context(session: AsyncSession, proposals: list[Proposal]) -> PageContext:
    item_ids: set[int] = set()
    control_ids: set[int] = set()
    for proposal in proposals:
        payload = proposal.payload or {}
        if proposal.kind == ProposalKind.MAPPING:
            if (value := _int(payload.get("framework_item_id"))) is not None:
                item_ids.add(value)
            if (value := _int(payload.get("control_id"))) is not None:
                control_ids.add(value)
        elif proposal.kind == ProposalKind.RELATION:
            for key in ("from_control_id", "to_control_id"):
                if (value := _int(payload.get(key))) is not None:
                    control_ids.add(value)

    items = {
        item.id: item
        for item in (
            await session.scalars(select(FrameworkItem).where(FrameworkItem.id.in_(item_ids)))
            if item_ids else []
        )
    }
    controls = {
        control.id: control
        for control in (
            await session.scalars(select(Control).where(Control.id.in_(control_ids)))
            if control_ids else []
        )
    }
    item_mappings: dict[int, list[tuple[str, str]]] = {}
    if item_ids:
        rows = await session.execute(
            select(Mapping.framework_item_id, Control.code, Mapping.strength)
            .join(Control, Control.id == Mapping.control_id)
            .where(Mapping.framework_item_id.in_(item_ids))
            .order_by(Control.code)
        )
        for item_id, code, strength in rows:
            item_mappings.setdefault(item_id, []).append(
                (code, strength.value if hasattr(strength, "value") else str(strength))
            )

    control_sources: dict[int, list[dict[str, Any]]] = {}
    if control_ids:
        rows = await session.execute(
            select(
                ControlSource.control_id,
                Clause.id,
                Clause.citation_label,
                Clause.heading_path,
                Document.id,
                Document.title,
            )
            .join(Clause, Clause.id == ControlSource.clause_id)
            .join(Document, Document.id == Clause.document_id)
            .where(ControlSource.control_id.in_(control_ids))
            .order_by(ControlSource.control_id, Clause.order_index)
        )
        for control_id, clause_id, label, path, document_id, title in rows:
            control_sources.setdefault(control_id, []).append({
                "clause_id": clause_id,
                "citation_label": label,
                "heading_path": path,
                "document_id": document_id,
                "document_title": title,
            })

    # 一次查完整页：按归一化正文建索引，比对规则与 materialize 共用同一个函数。
    wanted: dict[str, list[int]] = {}
    for proposal in proposals:
        if proposal.kind != ProposalKind.CONTROL_EXTRACT:
            continue
        key = normalise_statement(str((proposal.payload or {}).get("statement") or ""))
        if key:
            wanted.setdefault(key, []).append(proposal.id)
    statement_duplicates: dict[int, Control] = {}
    if wanted:
        rows = await session.execute(
            select(sql_normalised_statement().label("key"), Control)
            .where(sql_normalised_statement().in_(list(wanted)), Control.status == "active")
            .order_by(Control.id)
        )
        for key, control in rows:
            for proposal_id in wanted.get(key, []):
                statement_duplicates.setdefault(proposal_id, control)

    return PageContext(
        clauses=await clause_context(session, proposals),
        items=items,
        controls=controls,
        ocr_flagged=await service.ocr_flags(session, proposals),
        item_mappings=item_mappings,
        control_sources=control_sources,
        statement_duplicates=statement_duplicates,
        extraction_reviews=await service.semantic_reviews(session, proposals),
    )


def _enrich(citations: Any, context: dict[int, dict[str, Any]]) -> Any:
    """只做补全，绝不改写模型原始产出的字段。"""
    if not isinstance(citations, list):
        return citations
    enriched = []
    for citation in citations:
        if not isinstance(citation, dict):
            enriched.append(citation)
            continue
        # 条款可能已被删除；补不上就保留原样，不让整页 500。
        extra = {
            key: value
            for key, value in context.get(citation.get("clause_id"), {}).items()
            if not key.startswith("_")
        }
        enriched.append({**extra, **citation})
    return enriched


def _control_view(control: Control, context: PageContext) -> dict[str, Any]:
    return {
        "id": control.id,
        "code": control.code,
        "title": control.title,
        "statement": control.statement,
        # 控制点是抽取产物，不是原文。审核者要能一眼看到它出自哪份制度、哪一条，
        # 并点回去核对——判断「这条控制点是否满足某个框架要求」的前提，是先确认
        # 这条控制点本身如实反映了原文。
        "sources": context.control_sources.get(control.id, []),
    }


def present(proposal: Proposal, limits: Thresholds, context: PageContext) -> ProposalOut:
    """纯渲染，不查库——旁路数据全部来自 PageContext。"""
    payload = proposal.payload or {}
    flag = proposal.id in context.ocr_flagged
    twin = context.statement_duplicates.get(proposal.id)
    acceptable = service.eligible(
        proposal, limits, ocr_flag=flag, statement_duplicate=twin is not None,
        semantic_issue=proposal.id in context.extraction_reviews,
    )

    mapping: dict[str, Any] | None = None
    if proposal.kind == ProposalKind.MAPPING:
        item = context.items.get(_int(payload.get("framework_item_id")))
        control = context.controls.get(_int(payload.get("control_id")))
        if item is not None and control is not None:
            confirmed = context.item_mappings.get(item.id, [])
            closing = {strength.value for strength in CLOSING}
            mapping = {
                "framework_item": {
                    "id": item.id,
                    "code": item.code,
                    "title": item.title,
                    "description": item.description,
                },
                "control": _control_view(control, context),
                "item_coverage": {
                    # 已被 full/partial 关掉：再加一条不会改变覆盖度，
                    # 而 supporting 连差距清单上的标记都不会新增。
                    "closed": any(strength in closing for _, strength in confirmed),
                    "confirmed": [
                        {"control_code": code, "strength": strength}
                        for code, strength in confirmed
                    ],
                },
            }

    relation: dict[str, Any] | None = None
    exact_duplicate = False
    if proposal.kind == ProposalKind.RELATION:
        left = context.controls.get(_int(payload.get("from_control_id")))
        right = context.controls.get(_int(payload.get("to_control_id")))
        if left is not None and right is not None:
            exact_duplicate = (
                " ".join(left.statement.split()).casefold()
                == " ".join(right.statement.split()).casefold()
            )
            relation = {
                "relation_type": payload.get("relation_type"),
                "from": _control_view(left, context),
                "to": _control_view(right, context),
            }

    # 闸 4 保证「没有编造文字」，不保证「忠实转述」：被改的情态动词在生成的
    # statement 里、不在引文里，引用校验查不到。只标记不拦截——SLA 表格本来就
    # 不含情态词，做成硬闸门会把合理的表格转写全拒掉。
    drift = False
    if proposal.kind == ProposalKind.CONTROL_EXTRACT:
        cited = [
            context.clauses.get(_int(c.get("clause_id")), {}).get("_text", "")
            for c in (proposal.citations or [])
            if isinstance(c, dict)
        ]
        drift = upgraded_from(payload.get("statement"), cited)

    context_complete = (
        mapping is not None if proposal.kind == ProposalKind.MAPPING
        else relation is not None if proposal.kind == ProposalKind.RELATION
        else True
    )
    assessment = service.assess(
        proposal,
        limits,
        ocr_flag=flag,
        context_complete=context_complete,
        exact_duplicate=exact_duplicate,
    )
    return ProposalOut.model_validate(proposal).model_copy(
        update={
            "bulk_acceptable": acceptable,
            "normative_drift": drift,
            "extraction_review": context.extraction_reviews.get(proposal.id),
            "duplicate_of": (
                {"id": twin.id, "code": twin.code, "title": twin.title}
                if twin is not None else None
            ),
            "ocr_quality_flag": flag,
            "mapping_context": mapping,
            "relation_context": relation,
            "review_tier": assessment.tier,
            "review_reasons": list(assessment.reasons),
            "citations": _enrich(proposal.citations, context.clauses),
        }
    )


@router.get("", response_model=list[ProposalOut])
async def list_pending(
    *,
    kind: ProposalKind | None = None,
    document_id: int | None = Query(default=None, gt=0),
    strength: Annotated[list[MappingStrength] | None, Query()] = None,
    framework: Annotated[str | None, Query(max_length=64)] = None,
    doubtful_rationale: bool = False,
    actionable_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    actor: Annotated[User, Depends(require(Permission.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProposalOut]:
    rows = await service.pending(
        session,
        engagement_scope_id=actor.engagement_scope_id,
        kind=kind,
        document_id=document_id,
        strength=[value.value for value in strength] if strength else None,
        framework=framework,
        doubtful_rationale=doubtful_rationale,
        limit=2000 if actionable_only else limit,
    )
    limits = await load(session)
    context = await page_context(session, rows)
    output = [present(row, limits, context) for row in rows]
    if actionable_only:
        output = [
            proposal
            for proposal in output
            if proposal.review_tier in (service.ReviewTier.SAMPLE, service.ReviewTier.MANUAL)
        ]
    return output[:limit]


@router.get("/failures")
async def list_failures(
    *,
    document_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    _: Annotated[User, Depends(require(Permission.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, object]]:
    """抽取失败的批次。

    单开一个接口而不是塞进待确认队列：这些行**没有东西可供人决策**，
    混进去会让"还剩几条要看"这个数字失去意义。但它们必须能被看见——
    不然这一批就静默消失了（OQ-22）。
    """
    stmt = (
        select(Proposal)
        .where(Proposal.status == ProposalStatus.FAILED)
        .order_by(Proposal.id.desc())
        .limit(limit)
    )
    if document_id is not None:
        stmt = stmt.where(Proposal.document_id == document_id)
    return [
        {
            "id": row.id,
            "kind": row.kind.value,
            "document_id": row.document_id,
            "llm_call_id": row.llm_call_id,
            "reject_reason": row.reject_reason,
            "clause_ids": (row.payload or {}).get("clause_ids", []),
            "created_at": row.created_at,
        }
        for row in await session.scalars(stmt)
    ]


@router.get("/stats")
async def stats(
    _: Annotated[User, Depends(require(Permission.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    rows = await session.execute(
        select(Proposal.kind, func.count())
        .where(Proposal.status == ProposalStatus.PENDING)
        .group_by(Proposal.kind)
    )
    by_kind = {kind.value: count for kind, count in rows}
    # 失败批次单独计数：它不是待办，但界面要知道该不该提示"有批次没跑出来"
    failed = await session.scalar(
        select(func.count()).select_from(Proposal).where(
            Proposal.status == ProposalStatus.FAILED
        )
    )
    return {
        "pending": sum(by_kind.values()),
        "by_kind": by_kind,
        "failed": int(failed or 0),
    }


@router.post("/bulk-accept")
async def bulk_accept(
    payload: BulkAcceptIn,
    actor: Annotated[User, Depends(require(Permission.REVIEW_DECIDE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    try:
        result = await service.bulk_accept(session, payload.ids, actor=actor)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    if result["accepted"]:
        await enqueue("embed_controls")
    return result


@router.post("/auto-process")
async def auto_process(
    payload: AutoProcessIn,
    actor: Annotated[User, Depends(require(Permission.REVIEW_DECIDE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    try:
        result = await service.auto_process(
            session, actor=actor, limit=payload.limit, ids=payload.ids
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return result


# 预览每种类型最多扫这么多条。执行只认预览清单里的 id，所以它只影响预览上的数字。
PREVIEW_SCAN_LIMIT = 2000


@router.get("/auto-process/preview")
async def auto_process_preview(
    _: Annotated[User, Depends(require(Permission.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """只读的影响预览，用的是 `auto_process()` 那一套一模一样的判据。"""
    # 按类型分开取：原来先取置信度最低的 N 条（不分类型）再过滤，控制点抽取提案一多，
    # 映射与关系会被整批挤出预览。截断与否按过滤之前、每种类型各自的条数算。
    mappings = await service.pending(session, kind=ProposalKind.MAPPING, limit=PREVIEW_SCAN_LIMIT)
    relations = await service.pending(session, kind=ProposalKind.RELATION, limit=PREVIEW_SCAN_LIMIT)
    truncated = PREVIEW_SCAN_LIMIT in (len(mappings), len(relations))
    rows = [*mappings, *relations]
    limits = await load(session)
    context = await page_context(session, rows)
    proposals = [present(row, limits, context) for row in rows]

    framework_ids = {item.framework_id for item in context.items.values()}
    framework_keys = {
        framework.id: framework.key
        for framework in (
            await session.scalars(
                select(Framework).where(Framework.id.in_(framework_ids))
            )
            if framework_ids else []
        )
    }
    by_tier = {tier.value: 0 for tier in service.ReviewTier}
    by_kind: dict[str, dict[str, int]] = {}
    by_framework: dict[str, dict[str, int]] = {}
    by_reason: dict[str, int] = {}
    auto_items: list[dict[str, object]] = []
    sample_items: list[dict[str, object]] = []

    for proposal in proposals:
        tier = proposal.review_tier.value
        by_tier[tier] += 1
        kind_counts = by_kind.setdefault(
            proposal.kind.value, {value.value: 0 for value in service.ReviewTier}
        )
        kind_counts[tier] += 1
        for reason in proposal.review_reasons:
            by_reason[reason] = by_reason.get(reason, 0) + 1

        payload = proposal.payload
        summary: dict[str, object] = {
            "id": proposal.id,
            "kind": proposal.kind.value,
            "confidence": proposal.confidence,
        }
        if proposal.kind == ProposalKind.MAPPING:
            item = context.items.get(_int(payload.get("framework_item_id")))
            control = context.controls.get(_int(payload.get("control_id")))
            if item is not None:
                framework_key = framework_keys.get(item.framework_id, "unknown")
                framework_counts = by_framework.setdefault(
                    framework_key, {value.value: 0 for value in service.ReviewTier}
                )
                framework_counts[tier] += 1
                summary["target"] = item.code
            if control is not None:
                summary["source"] = control.code
            summary["strength"] = payload.get("strength")
        else:
            left = context.controls.get(_int(payload.get("from_control_id")))
            right = context.controls.get(_int(payload.get("to_control_id")))
            summary.update({
                "source": left.code if left else payload.get("from_control_id"),
                "target": right.code if right else payload.get("to_control_id"),
                "relation_type": payload.get("relation_type"),
            })
        if proposal.review_tier == service.ReviewTier.AUTO and len(auto_items) < 200:
            auto_items.append(summary)
        elif proposal.review_tier == service.ReviewTier.SAMPLE and len(sample_items) < 200:
            sample_items.append(summary)

    return {
        "scanned": len(proposals),
        "truncated": truncated,
        "by_tier": by_tier,
        "by_kind": by_kind,
        "by_framework": by_framework,
        "by_reason": by_reason,
        "auto_items": auto_items,
        "sample_items": sample_items,
        "estimated_changes": {
            "mappings": by_kind.get("mapping", {}).get("auto", 0),
            "relations": by_kind.get("relation", {}).get("auto", 0),
        },
    }


@router.post("/{proposal_id}/decide", response_model=ProposalOut)
async def decide(
    proposal_id: int,
    payload: DecideIn,
    actor: Annotated[User, Depends(require(Permission.REVIEW_DECIDE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProposalOut:
    try:
        proposal = await service.decide(
            session,
            proposal_id,
            actor=actor,
            decision=payload.decision,
            payload=payload.payload,
            reason=payload.reason,
            merge_into_control_id=payload.merge_into_control_id,
        )
        output = present(
            proposal, await load(session), await page_context(session, [proposal])
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    if (
        proposal.kind == ProposalKind.CONTROL_EXTRACT
        and payload.decision != service.Decision.REJECT
    ):
        await enqueue("embed_controls")
    return output
