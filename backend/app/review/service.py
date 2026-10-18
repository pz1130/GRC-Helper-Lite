import hashlib
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Integer, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.controls.models import Control, ControlSource
from app.errors import AppError, Conflict, Forbidden, NotFound, describe_validation
from app.extraction.models import ExtractionInventory
from app.frameworks.models import FrameworkItem
from app.iam.audit import record
from app.iam.models import User
from app.iam.permissions import Permission, has_permission
from app.ingest.models import Document
from app.review import thresholds as thresholds_module
from app.review.materialize import (
    ControlPayload,
    find_statement_duplicate,
    lock_control_writes,
    materialize,
)
from app.review.models import Proposal, ProposalKind, ProposalStatus

# 模型在 rationale 里自己写了否定表述，却仍把强度标成 full/partial 的那批。
# 实测 281 条 partial 里 101 条命中，其中 25 条置信度仍在 0.75 以上——是最可能
# 标错的一批。这是**关键词启发式**，不是判定：有些 does not 出现在无害的从句里。
# 界面上必须如实标成「启发式」，别让人当成结论。
RATIONALE_DOUBT = r"does not|doesn't|not specifically|no explicit|not address|lacks|absent"


class Decision(StrEnum):
    ACCEPT = "accept"
    MODIFY = "modify"
    REJECT = "reject"


class ReviewTier(StrEnum):
    AUTO = "auto"
    SAMPLE = "sample"
    MANUAL = "manual"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class ReviewAssessment:
    tier: ReviewTier
    reasons: tuple[str, ...]


def _sampled(proposal: Proposal, rate: float) -> bool:
    """稳定抽样：同一页反复加载，确认集合不会变。"""
    if not math.isfinite(rate) or rate <= 0:
        return False
    if rate >= 1:
        return True
    key = f"{proposal.kind.value}:{proposal.id}".encode()
    bucket = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / 2**64
    return bucket < rate


def assess(
    proposal: Proposal,
    limits: thresholds_module.Thresholds,
    *,
    ocr_flag: bool,
    context_complete: bool = True,
    exact_duplicate: bool = False,
) -> ReviewAssessment:
    """给 AI 提案定级，把拿不准的挡在正式数据之外。"""
    if proposal.status != ProposalStatus.PENDING:
        return ReviewAssessment(ReviewTier.MANUAL, ("already_decided",))
    if proposal.kind not in (ProposalKind.MAPPING, ProposalKind.RELATION):
        return ReviewAssessment(ReviewTier.MANUAL, ("unsupported_kind",))
    if not context_complete:
        return ReviewAssessment(ReviewTier.MANUAL, ("missing_context",))
    if ocr_flag:
        return ReviewAssessment(ReviewTier.MANUAL, ("ocr_suspect",))

    confidence = proposal.confidence
    high_confidence = (
        type(confidence) in (int, float)
        and math.isfinite(confidence)
        and 0 <= limits.force_manual <= limits.auto_accept <= 1
        and confidence >= limits.auto_accept
    )
    reasons: list[str] = []
    eligible = False
    if proposal.kind == ProposalKind.MAPPING:
        doubtful = bool(
            re.search(RATIONALE_DOUBT, str(proposal.payload.get("rationale", "")), re.IGNORECASE)
        )
        if doubtful:
            reasons.append("hedged_rationale")
        eligible = high_confidence and not doubtful
    else:
        relation_type = proposal.payload.get("relation_type")
        eligible = high_confidence and relation_type == "duplicates" and exact_duplicate
        if relation_type == "depends_on":
            reasons.append("directional_relation")
        elif not exact_duplicate:
            reasons.append("non_exact_duplicate")

    # force_manual 曾经是个不生效的开关：设置页写着「低于此值必须逐条确认」、保存时
    # 还校验它必须小于自动接受阈值，但代码里它只出现在顺序合法性判断里，调它什么都
    # 不会变。它不会造成坏数据（低于 auto_accept 的本来就进不了 AUTO），造成的是
    # **错误的安全感**。provider 级预算原先也是这样的死字段，后来改成了真生效，同办。
    below_force_manual = (
        type(confidence) in (int, float)
        and math.isfinite(confidence)
        and 0 <= limits.force_manual <= limits.auto_accept <= 1
        and confidence < limits.force_manual
    )
    if below_force_manual:
        return ReviewAssessment(ReviewTier.MANUAL, ("below_force_manual",))

    if eligible:
        if _sampled(proposal, limits.review_sample_rate):
            return ReviewAssessment(ReviewTier.SAMPLE, ("continuous_sample",))
        return ReviewAssessment(ReviewTier.AUTO, ("validated_high_confidence",))
    if _sampled(proposal, limits.review_sample_rate):
        return ReviewAssessment(ReviewTier.SAMPLE, tuple(reasons or ["calibration_sample"]))
    if confidence is None or not high_confidence:
        reasons.append("below_auto_threshold")
    return ReviewAssessment(ReviewTier.DEFERRED, tuple(dict.fromkeys(reasons)))


_STATUS = {
    Decision.ACCEPT: ProposalStatus.ACCEPTED,
    Decision.MODIFY: ProposalStatus.MODIFIED,
    Decision.REJECT: ProposalStatus.REJECTED,
}


async def create(
    session: AsyncSession,
    *,
    kind: ProposalKind,
    payload: dict[str, Any],
    citations: list[dict[str, Any]],
    confidence: float | None = None,
    llm_call_id: int | None = None,
    document_id: int | None = None,
    actor: User | None = None,
) -> Proposal:
    """留审计的提案创建；事务与 worker 鉴权由调用方负责。"""
    if not isinstance(payload, dict) or not isinstance(citations, list):
        raise AppError("提案内容及引用格式无效", code="proposal.payload_invalid")
    if confidence is not None and (
        type(confidence) not in (int, float)
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        raise AppError("置信度必须在 0 到 1 之间", code="proposal.confidence_out_of_range")
    if kind == ProposalKind.CONTROL_EXTRACT:
        try:
            ControlPayload.model_validate(payload)
        except ValidationError as exc:
            raise AppError(f"控制点提案无效：{exc}", code="proposal.control_invalid", params={"detail": describe_validation(exc.errors())}) from exc
        if payload.get("citations") != citations:
            raise AppError("提案引用与内容不一致", code="proposal.citation_mismatch")
        from app.extraction.citations import ClauseCitationValidator

        reason = await ClauseCitationValidator(session, document_id=document_id).check(
            {"controls": [payload]}
        )
        if reason:
            raise AppError(f"引用校验失败：{reason}", code="citation.check_failed", params={"reason": reason})
    elif kind == ProposalKind.MAPPING:
        from app.mapping.citations import MappingCitationValidator

        item_id = payload.get("framework_item_id")
        if type(item_id) is not int:
            raise AppError("映射提案缺少 framework_item_id", code="proposal.mapping_item_missing")
        reason = await MappingCitationValidator(session, item_ids={item_id}).check(
            {"mappings": [payload]}
        )
        if reason:
            raise AppError(f"引用校验失败：{reason}", code="citation.check_failed", params={"reason": reason})
    elif kind == ProposalKind.RELATION:
        from app.relations.citations import RelationCitationValidator

        ends = (payload.get("from_control_id"), payload.get("to_control_id"))
        if any(type(value) is not int for value in ends):
            raise AppError("关系提案缺少控制点编号", code="proposal.relation_control_missing")
        reason = await RelationCitationValidator(session, control_ids=set(ends)).check(
            {"relations": [payload]}
        )
        if reason:
            raise AppError(f"引用校验失败：{reason}", code="citation.check_failed", params={"reason": reason})
    elif kind == ProposalKind.ANSWER:
        from app.audit.citations import AnswerCitationValidator
        from app.audit.schemas import AnswerProposalPayload

        try:
            AnswerProposalPayload.model_validate(payload)
        except ValidationError as exc:
            raise AppError(f"审计答复提案无效：{exc}", code="proposal.answer_invalid", params={"detail": describe_validation(exc.errors())}) from exc
        if payload.get("citations") != citations:
            raise AppError("答复提案引用与内容不一致", code="proposal.answer_citation_mismatch")
        clause_ids = {
            citation.get("clause_id")
            for citation in citations
            if isinstance(citation, dict) and type(citation.get("clause_id")) is int
        }
        reason = await AnswerCitationValidator(
            session, allowed_clause_ids=clause_ids
        ).check(payload)
        if reason:
            raise AppError(f"引用校验失败：{reason}", code="citation.check_failed", params={"reason": reason})
    proposal = Proposal(
        kind=kind,
        payload=deepcopy(payload),
        citations=deepcopy(citations),
        confidence=confidence,
        llm_call_id=llm_call_id,
        document_id=document_id,
        status=ProposalStatus.PENDING,
    )
    session.add(proposal)
    await session.flush()
    await record(
        session,
        user=actor,
        action="proposal.create",
        entity_type="Proposal",
        entity_id=proposal.id,
        after={"kind": kind.value, "llm_call_id": llm_call_id, "document_id": document_id},
    )
    await session.flush()
    return proposal


async def record_failed_batch(
    session: AsyncSession,
    *,
    kind: ProposalKind,
    reason: str,
    document_id: int | None = None,
    llm_call_id: int | None = None,
    batch_fingerprint: str | None = None,
    clause_ids: list[int] | None = None,
) -> Proposal:
    """给一个什么都没产出的批次留一行占位提案（OQ-22）。

    为什么走这里而不是在抽取任务里直接 `Proposal(...)`：所有 Proposal 的构造都
    只能在这个模块里发生，`tests/test_extraction_tasks.py` 有一条 AST 守卫在盯着
    （抽取任务直接建业务对象就是绕开审计）。

    它与 `create()` 的区别是**没有内容可校验**：payload 里装的是批次标识而不是
    控制点，所以不能走那套引用校验。status 直接落 FAILED——它不是待人决策的
    提案，不该进待确认计数。
    """
    proposal = Proposal(
        kind=kind,
        payload={"batch_fingerprint": batch_fingerprint, "clause_ids": clause_ids or []},
        citations=[],
        status=ProposalStatus.FAILED,
        reject_reason=reason,
        llm_call_id=llm_call_id,
        document_id=document_id,
    )
    session.add(proposal)
    await session.flush()
    await record(
        session,
        user=None,
        action="proposal.batch_failed",
        entity_type="Proposal",
        entity_id=proposal.id,
        after={"kind": kind.value, "document_id": document_id, "reason": reason},
    )
    await session.flush()
    return proposal


async def has_failed_batch(session: AsyncSession, batch_fingerprint: str) -> bool:
    """这个批次是否已经有一行还挂在队列里的失败占位。

    worker 对失败任务会自动重试；provider 持续故障时每次重试都会走到同一批次，
    不查的话同一批次会堆出好几行一模一样的失败。
    """
    found = await session.scalar(
        select(Proposal.id).where(
            Proposal.status == ProposalStatus.FAILED,
            Proposal.payload["batch_fingerprint"].astext == batch_fingerprint,
        ).limit(1)
    )
    return found is not None


async def supersede_failed_batch(session: AsyncSession, batch_fingerprint: str) -> None:
    """同一批次重跑成功后，把旧的失败占位行移出队列。

    **不 DELETE**：`AuditLog` 按 id 记录实体，删行会让审计指向空气（铁律 2）。
    改成 rejected 并在原因后面注明，行还在、队列干净。

    用一条定向 UPDATE 而不是"查出来再逐个改"：每个成功批次都会调它，绝大多数
    时候一行都匹配不上，没必要为此把 ORM 对象物化一遍。
    """
    await session.execute(
        update(Proposal)
        .where(
            Proposal.status == ProposalStatus.FAILED,
            Proposal.payload["batch_fingerprint"].astext == batch_fingerprint,
        )
        .values(
            status=ProposalStatus.REJECTED,
            reject_reason=func.concat(
                func.coalesce(Proposal.reject_reason, ""), "（该批次已重跑成功，本行留档）"
            ),
        )
    )


def require_actor(actor: User, permission: Permission = Permission.REVIEW_DECIDE) -> None:
    if (
        not actor.is_active
        or not has_permission(actor.role, permission)
        or (actor.expires_at is not None and actor.expires_at <= datetime.now(UTC))
    ):
        raise Forbidden(
            f"当前账号无权执行该操作（需要 {permission.value}）",
            code="auth.permission_required",
            params={"permission": permission.value},
        )


def validate_decision(
    decision: Decision, payload: dict[str, Any] | None, reason: str | None
) -> None:
    if decision == Decision.MODIFY:
        if not isinstance(payload, dict) or not payload:
            raise AppError("修改后接受必须给出改后的内容", code="decision.payload_required")
    elif payload is not None:
        raise AppError("仅修改后接受允许提供内容", code="decision.payload_forbidden")
    if decision == Decision.REJECT:
        if not isinstance(reason, str) or not reason.strip():
            raise AppError("拒绝必须给出原因", code="decision.reason_required")
    elif reason is not None:
        raise AppError("仅拒绝允许提供原因", code="decision.reason_forbidden")


# 不进确认队列的提案：RCSA 预填建议由作答人在评估页上逐项应用，不需要也不允许由
# 确认队列裁定（那里的裁定权是 grc_lead 的，作答人是 contributor）。
OUTSIDE_QUEUE = (ProposalKind.RCSA_PREFILL,)


async def pending(
    session: AsyncSession,
    *,
    kind: ProposalKind | None = None,
    document_id: int | None = None,
    strength: list[str] | None = None,
    framework: str | None = None,
    doubtful_rationale: bool = False,
    limit: int = 50,
    engagement_scope_id: int | None = None,
) -> list[Proposal]:
    if not 1 <= limit <= 2000:
        raise AppError("limit 必须在 1 到 2000 之间", code="review.limit_out_of_range")
    stmt = (
        select(Proposal)
        .where(Proposal.status == ProposalStatus.PENDING, Proposal.kind.notin_(OUTSIDE_QUEUE))
        .order_by(Proposal.confidence.asc().nullsfirst(), Proposal.id)
        .limit(limit)
    )
    if kind is not None:
        stmt = stmt.where(Proposal.kind == kind)
    if document_id is not None:
        stmt = stmt.where(Proposal.document_id == document_id)
    if strength:
        # 映射提案里只有 full/partial 影响覆盖度，supporting 不消除差距——
        # 审核 400 多条时，能只看影响结论的那些是刚需。
        stmt = stmt.where(Proposal.payload["strength"].astext.in_(strength))
    if framework:
        # 按框架筛：CSF 106 个要求项 vs 800-53 的 1014 个，两者的审阅回报差一个量级，
        # 能只看一个框架是把 400 多条切成可做的量的最有效一刀。
        from app.frameworks.models import Framework, FrameworkItem

        stmt = stmt.where(
            Proposal.payload["framework_item_id"].astext.cast(Integer).in_(
                select(FrameworkItem.id)
                .join(Framework, Framework.id == FrameworkItem.framework_id)
                .where(Framework.key == framework)
            )
        )
    if doubtful_rationale:
        stmt = stmt.where(Proposal.payload["rationale"].astext.op("~*")(RATIONALE_DOUBT))
    if engagement_scope_id is not None:
        # 限范围账号（外部审计员）：答复提案只留自己那个项目的问题。审计模块里处处
        # 按这个边界过滤，确认队列只要 READ，原来把所有项目的答复草稿都交了出去。
        from app.audit.models import AuditQuestion

        stmt = stmt.where(
            (Proposal.kind != ProposalKind.ANSWER)
            | Proposal.payload["question_id"].astext.cast(Integer).in_(
                select(AuditQuestion.id).where(AuditQuestion.engagement_id == engagement_scope_id)
            )
        )
    return list(await session.scalars(stmt))


async def locked_proposal(session: AsyncSession, proposal_id: int) -> Proposal | None:
    # 取到锁之后要刷掉身份缓存：这中间可能已经有别的事务把它裁定了。
    return await session.scalar(
        select(Proposal)
        .where(Proposal.id == proposal_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def citation_clause_ids(proposal: Proposal) -> set[int]:
    ids: set[int] = set()
    for citations in (proposal.citations, proposal.payload.get("citations", [])):
        if isinstance(citations, list):
            ids.update(
                c["clause_id"]
                for c in citations
                if isinstance(c, dict) and type(c.get("clause_id")) is int
            )
    return ids


def proposal_control_ids(proposal: Proposal) -> set[int]:
    keys = (
        ("control_id",) if proposal.kind == ProposalKind.MAPPING
        else ("from_control_id", "to_control_id")
    )
    return {
        value
        for key in keys
        if type(value := proposal.payload.get(key)) is int
    }


async def ocr_flag(session: AsyncSession, proposal: Proposal) -> bool:
    return proposal.id in await ocr_flags(session, [proposal])


async def ocr_flags(
    session: AsyncSession, proposals: list[Proposal]
) -> frozenset[int]:
    """整页两次查完 OCR 存疑标记，返回命中的提案 id。

    逐条查时 412 条映射提案就是 412 次往返。这里先把所有引用条款一次映射到
    文档，再一次查出其中哪些文档存疑，剩下的判断在内存里做。
    """
    if not proposals:
        return frozenset()
    wanted_clauses = {cid for p in proposals for cid in citation_clause_ids(p)}
    control_ids = {cid for p in proposals for cid in proposal_control_ids(p)}
    proposal_controls = {p.id: proposal_control_ids(p) for p in proposals}
    control_clauses: dict[int, set[int]] = {}
    if control_ids:
        rows = await session.execute(
            select(ControlSource.control_id, ControlSource.clause_id).where(
                ControlSource.control_id.in_(control_ids)
            )
        )
        for control_id, clause_id in rows:
            control_clauses.setdefault(control_id, set()).add(clause_id)
            wanted_clauses.add(clause_id)
    clause_document: dict[int, int] = {}
    if wanted_clauses:
        rows = await session.execute(
            select(Clause.id, Clause.document_id).where(Clause.id.in_(wanted_clauses))
        )
        clause_document = dict(rows.all())

    documents = {p.document_id for p in proposals if p.document_id is not None}
    documents.update(clause_document.values())
    if not documents:
        return frozenset()
    flagged = set(
        await session.scalars(
            select(Document.id).where(
                Document.id.in_(documents), Document.ocr_quality_flag.is_(True)
            )
        )
    )
    if not flagged:
        return frozenset()

    hit = set()
    for proposal in proposals:
        touched = {clause_document[cid]
                   for cid in citation_clause_ids(proposal) if cid in clause_document}
        for control_id in proposal_controls[proposal.id]:
            touched.update(
                clause_document[clause_id]
                for clause_id in control_clauses.get(control_id, set())
                if clause_id in clause_document
            )
        if proposal.document_id is not None:
            touched.add(proposal.document_id)
        if touched & flagged:
            hit.add(proposal.id)
    return frozenset(hit)


async def semantic_reviews(session: AsyncSession, proposals: list[Proposal]) -> dict[int, dict]:
    call_ids = {p.llm_call_id for p in proposals
                if p.kind == ProposalKind.CONTROL_EXTRACT and p.llm_call_id is not None}
    if not call_ids:
        return {}
    inventories = list(await session.scalars(select(ExtractionInventory).where(
        ExtractionInventory.review_call_id.in_(call_ids),
    )))
    by_call = {entry.review_call_id: entry for entry in inventories}
    result = {}
    for proposal in proposals:
        entry = by_call.get(proposal.llm_call_id)
        if entry is not None and entry.document_id == proposal.document_id:
            findings = (entry.semantic_review or {}).get("findings", [])
            if findings:
                result[proposal.id] = {"inventory_id": entry.id, "document_id": entry.document_id,
                                       "finding_count": len(findings)}
    return result


def eligible(
    proposal: Proposal,
    limits: thresholds_module.Thresholds,
    *,
    ocr_flag: bool,
    statement_duplicate: bool = False,
    semantic_issue: bool = False,
) -> bool:
    """纯函数：给定 OCR 标记，这条提案能否批量接受。

    拆出来是为了让整页判定可以先批量查标记、再在内存里逐条判，
    不必每条提案都往库里跑一次。

    正文与已有控制点逐字相同的，一律禁止批量——理由与 OCR 存疑那条相同：
    它需要一个逐条的选择（并入还是另建），批量按钮给不了。
    """
    return (
        not statement_duplicate
        and not semantic_issue
        and proposal.status == ProposalStatus.PENDING
        and proposal.kind in (
            ProposalKind.CONTROL_EXTRACT, ProposalKind.MAPPING, ProposalKind.RELATION
        )
        and proposal.payload.get("origin") != "matrix"
        and thresholds_module.bulk_acceptable(proposal, limits, ocr_flag=ocr_flag)
    )


async def eligibility(
    session: AsyncSession, proposal: Proposal, limits: thresholds_module.Thresholds
) -> tuple[bool, bool]:
    flag = await ocr_flag(session, proposal)
    duplicate = None
    if proposal.kind == ProposalKind.CONTROL_EXTRACT:
        duplicate = await find_statement_duplicate(
            session, str((proposal.payload or {}).get("statement") or "")
        )
    return (
        eligible(proposal, limits, ocr_flag=flag, statement_duplicate=duplicate is not None,
                 semantic_issue=proposal.id in await semantic_reviews(session, [proposal])),
        flag,
    )


async def decide(
    session: AsyncSession,
    proposal_id: int,
    *,
    actor: User,
    decision: Decision,
    payload: dict[str, Any] | None = None,
    reason: str | None = None,
    merge_into_control_id: int | None = None,
) -> Proposal:
    require_actor(actor)
    try:
        decision = Decision(decision)
    except (ValueError, TypeError) as exc:
        raise AppError("无效的裁定操作", code="decision.invalid") from exc
    try:
        async with session.begin_nested():
            await lock_control_writes(session)
            proposal = await locked_proposal(session, proposal_id)
            if proposal is None:
                raise NotFound("提案不存在", code="proposal.not_found")
            if proposal.kind in OUTSIDE_QUEUE:
                raise Conflict("这类建议只在它所属的页面上处理", code="review.kind_not_in_queue")
            if proposal.status != ProposalStatus.PENDING:
                raise Conflict(
                    f"该提案已被裁定为 {proposal.status.value}，不能重复裁定",
                    code="proposal.already_decided",
                    params={"status": proposal.status.value},
                )
            validate_decision(decision, payload, reason)
            before = {"status": proposal.status.value}
            if decision != Decision.REJECT:
                data = deepcopy(payload if decision == Decision.MODIFY else proposal.payload)
                await materialize(
                    session,
                    proposal,
                    data,
                    actor_id=actor.id,
                    merge_into_control_id=merge_into_control_id,
                )
                # 精确留存「批准的是什么」；AI 的原始 payload 保持不可变。
                proposal.decided_payload = data
            proposal.status = _STATUS[decision]
            proposal.decided_by = actor.id
            proposal.decided_at = datetime.now(UTC)
            proposal.reject_reason = reason.strip() if decision == Decision.REJECT else None
            await record(
                session,
                user=actor,
                action=f"proposal.{decision.value}",
                entity_type="Proposal",
                entity_id=proposal.id,
                before=before,
                after={
                    "kind": proposal.kind.value,
                    "status": proposal.status.value,
                    "decided_payload": proposal.decided_payload,
                    "reject_reason": proposal.reject_reason,
                    "merged_into_control_id": merge_into_control_id,
                },
            )
            await session.flush()
        return proposal
    except IntegrityError as exc:
        raise Conflict("确认内容与现有数据冲突，请刷新后重试", code="proposal.stale_conflict") from exc


async def bulk_accept(
    session: AsyncSession, proposal_ids: list[int], *, actor: User
) -> dict[str, int]:
    require_actor(actor)
    if (
        not proposal_ids
        or len(proposal_ids) > 200
        or any(type(value) is not int or value <= 0 for value in proposal_ids)
        or len(set(proposal_ids)) != len(proposal_ids)
    ):
        raise AppError("提案编号必须为 1 到 200 个不同的正整数", code="review.bulk_ids_invalid")
    limits = await thresholds_module.load(session)
    accepted = skipped = 0
    # 内容非法就整批不落；不合格或不存在的行计为跳过。
    # 行锁顺序固定，避免交叠的批次之间死锁。
    async with session.begin_nested():
        await lock_control_writes(session)
        for proposal_id in sorted(proposal_ids):
            proposal = await locked_proposal(session, proposal_id)
            if proposal is None:
                skipped += 1
                continue
            acceptable, _ = await eligibility(session, proposal, limits)
            if not acceptable:
                skipped += 1
                continue
            await decide(session, proposal_id, actor=actor, decision=Decision.ACCEPT)
            accepted += 1
    return {"accepted": accepted, "skipped": skipped}


async def auto_process(
    session: AsyncSession, *, actor: User, limit: int = 200, ids: list[int] | None = None
) -> dict[str, int]:
    """只把窄而确定的那一档自动落库。

    发起的人记在批次动作上。**单条裁定归属于系统**（`decided_by` 为 NULL），
    这样自动化的结果永远不会被当成某个人的批准。

    `ids` 是预览里给人看过的清单：只在这些里面挑，并且逐条重新判档——预览之后
    状态变了的（被别人裁定、置信度阈值改了）照样不会被接受。接口要求必填；
    None 只留给直接调用的测试与脚本。"""
    require_actor(actor)
    if not 1 <= limit <= 200:
        raise AppError("limit 必须在 1 到 200 之间", code="review.bulk_limit_out_of_range")
    stmt = select(Proposal).where(
        Proposal.status == ProposalStatus.PENDING,
        Proposal.kind.in_((ProposalKind.MAPPING, ProposalKind.RELATION)),
    )
    if ids is not None:
        stmt = stmt.where(Proposal.id.in_(ids))
    proposals = list(await session.scalars(stmt.order_by(Proposal.id).limit(5000)))
    limits = await thresholds_module.load(session)
    flagged = await ocr_flags(session, proposals)

    control_ids = {value for p in proposals for value in proposal_control_ids(p)}
    controls = {
        control.id: control
        for control in (
            await session.scalars(select(Control).where(Control.id.in_(control_ids)))
            if control_ids else []
        )
    }
    item_ids = {
        value
        for p in proposals
        if p.kind == ProposalKind.MAPPING
        if type(value := p.payload.get("framework_item_id")) is int
    }
    existing_items = set(
        await session.scalars(
            select(FrameworkItem.id).where(FrameworkItem.id.in_(item_ids))
        )
    ) if item_ids else set()

    assessments: dict[int, ReviewAssessment] = {}
    for proposal in proposals:
        ids = proposal_control_ids(proposal)
        context_complete = ids <= controls.keys()
        exact_duplicate = False
        if proposal.kind == ProposalKind.MAPPING:
            context_complete = context_complete and proposal.payload.get("framework_item_id") in existing_items
        elif len(ids) == 2 and context_complete:
            left = controls[proposal.payload["from_control_id"]].statement
            right = controls[proposal.payload["to_control_id"]].statement
            exact_duplicate = " ".join(left.split()).casefold() == " ".join(right.split()).casefold()
        assessments[proposal.id] = assess(
            proposal,
            limits,
            ocr_flag=proposal.id in flagged,
            context_complete=context_complete,
            exact_duplicate=exact_duplicate,
        )

    counts = {tier.value: 0 for tier in ReviewTier}
    for assessment in assessments.values():
        counts[assessment.tier.value] += 1
    accepted = skipped = 0
    for proposal in proposals:
        assessment = assessments[proposal.id]
        if assessment.tier != ReviewTier.AUTO or accepted >= limit:
            continue
        try:
            async with session.begin_nested():
                await lock_control_writes(session)
                locked = await locked_proposal(session, proposal.id)
                if locked is None or locked.status != ProposalStatus.PENDING:
                    skipped += 1
                    continue
                data = deepcopy(locked.payload)
                await materialize(session, locked, data, actor_id=None)
                locked.status = ProposalStatus.ACCEPTED
                locked.decided_by = None
                locked.decided_at = datetime.now(UTC)
                locked.decided_payload = data
                await record(
                    session,
                    user=None,
                    action="proposal.auto_accept",
                    entity_type="Proposal",
                    entity_id=locked.id,
                    before={"status": ProposalStatus.PENDING.value},
                    after={
                        "status": ProposalStatus.ACCEPTED.value,
                        "policy": "validated_high_confidence_v1",
                        "reasons": list(assessment.reasons),
                    },
                )
                await session.flush()
            accepted += 1
        except (AppError, IntegrityError):
            skipped += 1

    await record(
        session,
        user=actor,
        action="proposal.auto_process",
        entity_type="ProposalBatch",
        entity_id="mapping-relation",
        after={"scanned": len(proposals), "accepted": accepted, "skipped": skipped, **counts},
    )
    await session.flush()
    return {"scanned": len(proposals), "accepted": accepted, "skipped": skipped, **counts}
