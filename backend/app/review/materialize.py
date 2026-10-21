"""**唯一**一个把确认后的控制点落成正式数据、并承接人工编辑的模块（铁律 1）。"""

import re
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.controls.merge import MergePlan
from app.controls.models import (
    Control,
    ControlRelation,
    ControlSource,
    RelationType,
    SourceRelation,
    active_controls,
)
from app.errors import AppError, Conflict, NotFound, describe_validation
from app.frameworks.models import FrameworkItem, Mapping, MappingStrength
from app.iam.models import AuditLog, User
from app.review.models import Proposal, ProposalKind, ProposalStatus

TextValue = Annotated[str, Field(strict=True, min_length=1)]
_PROVENANCE = ("origin", "matrix_mapping_proposal_id", "source_sha256", "row_number")


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    clause_id: Annotated[int, Field(strict=True, gt=0)]
    quote: TextValue
    relation: SourceRelation = SourceRelation.DEFINES


class ControlPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: Annotated[str, Field(strict=True, min_length=1, max_length=500)]
    statement: TextValue
    category: Annotated[str, Field(strict=True, max_length=100)] | None = None
    confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] | None = None
    citations: list[Citation]


class MatrixControlPayload(ControlPayload):
    code: Annotated[str, Field(strict=True, max_length=64)] | None = None
    owner: str | None = None
    framework_refs: str | None = None
    note: str | None = None
    origin: str
    matrix_mapping_proposal_id: Annotated[int, Field(strict=True, gt=0)]
    source_sha256: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
    row_number: Annotated[int, Field(strict=True, ge=1)]


class MappingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    framework_item_id: Annotated[int, Field(strict=True, gt=0)]
    control_id: Annotated[int, Field(strict=True, gt=0)]
    strength: MappingStrength
    framework_item_quote: TextValue
    rationale: Annotated[str, Field(strict=True, min_length=1, max_length=2000)]
    confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] | None = None


# M6 只推断这两种：conflicts_with 属 M10 的冲突检测，implements/refines 语义
# 相邻、模型容易混，人工抽查也难给出一致标准。
_M6_RELATIONS = (RelationType.DUPLICATES, RelationType.DEPENDS_ON)


class RelationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    from_control_id: Annotated[int, Field(strict=True, gt=0)]
    to_control_id: Annotated[int, Field(strict=True, gt=0)]
    relation_type: RelationType
    from_quote: TextValue
    to_quote: TextValue
    rationale: Annotated[str, Field(strict=True, min_length=1, max_length=2000)]
    confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] | None = None


async def _resolve_control_id(session: AsyncSession, control_id: int) -> int:
    """顺着 merged_into_id 走到仍生效的那条。外键拦不住环，所以自带上限。"""
    seen: set[int] = set()
    current_id = control_id
    hops = 0
    while True:
        if current_id in seen:
            raise AppError("控制点合并链成环或循环过长", code="control.merge_chain_cycle")
        seen.add(current_id)
        control = await session.get(Control, current_id)
        if control is None:
            raise NotFound("控制点不存在", code="control.not_found")
        if control.status != "merged" or control.merged_into_id is None:
            return control.id
        hops += 1
        if hops > 10:
            raise AppError("控制点合并链成环或循环过长", code="control.merge_chain_cycle")
        current_id = control.merged_into_id


async def _materialize_relation(
    session: AsyncSession, proposal: Proposal, data: dict[str, Any], *, actor_id: int | None
) -> None:
    from app.relations.citations import RelationCitationValidator

    try:
        validated = RelationPayload.model_validate(data)
    except ValidationError as exc:
        raise AppError(f"关系内容无效：{exc}", code="relation.payload_invalid", params={"detail": describe_validation(exc.errors())}) from exc
    if validated.relation_type not in _M6_RELATIONS:
        raise AppError(
            f"本里程碑不支持关系类型 {validated.relation_type.value}",
            code="relation.type_unsupported",
            params={"relation": validated.relation_type.value},
        )
    if validated.from_control_id == validated.to_control_id:
        raise AppError("关系的两端不能是同一个控制点", code="relation.same_control")

    ends = {validated.from_control_id, validated.to_control_id}
    reason = await RelationCitationValidator(session, control_ids=ends).check(
        {"relations": [data]}
    )
    if reason:
        raise AppError(f"引用校验失败：{reason}", code="citation.check_failed", params={"reason": reason})

    # 引文对着提案里的原 id（输家仍持有被引正文）；外键才解析到赢家。
    start = await _resolve_control_id(session, validated.from_control_id)
    end = await _resolve_control_id(session, validated.to_control_id)
    if start == end:
        raise AppError("合并后关系的两端是同一条控制点", code="relation.same_after_merge")
    # duplicates 对称：不规范化就会存下 A→B 与 B→A 两条互为镜像的记录，
    # (from, to, relation_type) 的唯一约束对它们无能为力。
    if validated.relation_type is RelationType.DUPLICATES and start > end:
        start, end = end, start

    await lock_control_writes(session)
    existing = await session.scalar(
        select(ControlRelation).where(
            ControlRelation.from_control_id == start,
            ControlRelation.to_control_id == end,
            ControlRelation.relation_type == validated.relation_type,
        )
    )
    if existing is not None:
        raise Conflict("这两个控制点之间已有同类型的确认关系", code="relation.already_confirmed")

    session.add(ControlRelation(
        from_control_id=start,
        to_control_id=end,
        relation_type=validated.relation_type,
        rationale=validated.rationale,
        confidence=proposal.confidence,
        confirmed_by=actor_id,
        confirmed_at=datetime.now(UTC),
    ))
    await session.flush()


async def _materialize_conflict(
    session: AsyncSession, proposal: Proposal, data: dict[str, Any], *, actor_id: int | None
) -> None:
    from app.conflicts.citations import ConflictCitationValidator
    from app.conflicts.models import PolicyConflict, normalise_pair
    from app.conflicts.schemas import ConflictPayload

    try:
        validated = ConflictPayload.model_validate(data)
    except ValidationError as exc:
        raise AppError(f"冲突内容无效：{exc}", code="conflict.payload_invalid", params={"detail": describe_validation(exc.errors())}) from exc
    if validated.clause_a_id == validated.clause_b_id:
        raise AppError("冲突的两端不能是同一条条款", code="conflict.same_clause")

    ends = {validated.clause_a_id, validated.clause_b_id}
    reason = await ConflictCitationValidator(session, clause_ids=ends).check(
        {"conflicts": [data]}
    )
    if reason:
        raise AppError(f"引用校验失败：{reason}", code="citation.check_failed", params={"reason": reason})

    # 冲突无方向：不规范化就会存下互为镜像的两行，唯一约束对它们无能为力。
    low, high = normalise_pair(validated.clause_a_id, validated.clause_b_id)
    topic = validated.topic.strip()

    existing = await session.scalar(
        select(PolicyConflict).where(
            PolicyConflict.clause_a_id == low,
            PolicyConflict.clause_b_id == high,
            PolicyConflict.topic == topic,
        )
    )
    if existing is not None:
        # 同一处冲突被两条提案各确认一次是正常的（两个批次都报了），不当错误。
        return

    session.add(
        PolicyConflict(
            clause_a_id=low,
            clause_b_id=high,
            topic=topic,
            difference=validated.difference,
            confidence=validated.confidence,
            confirmed_by=actor_id,
            confirmed_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def _materialize_mapping(
    session: AsyncSession, proposal: Proposal, data: dict[str, Any], *, actor_id: int | None
) -> None:
    from app.mapping.citations import MappingCitationValidator

    try:
        validated = MappingPayload.model_validate(data)
    except ValidationError as exc:
        raise AppError(f"映射内容无效：{exc}", code="mapping.payload_invalid", params={"detail": describe_validation(exc.errors())}) from exc

    item = await session.get(FrameworkItem, validated.framework_item_id)
    if item is None:
        raise AppError("框架项不存在", code="framework_item.not_found")
    reason = await MappingCitationValidator(
        session, item_ids={validated.framework_item_id}
    ).check({"mappings": [data]})
    if reason:
        raise AppError(f"引用校验失败：{reason}", code="citation.check_failed", params={"reason": reason})

    control_id = await _resolve_control_id(session, validated.control_id)

    await lock_control_writes(session)
    existing = await session.scalar(
        select(Mapping).where(
            Mapping.control_id == control_id,
            Mapping.framework_item_id == validated.framework_item_id,
        )
    )
    if existing is not None:
        raise Conflict("该控制点与框架项之间已有确认过的映射", code="mapping.already_confirmed")

    session.add(Mapping(
        control_id=control_id,
        framework_item_id=validated.framework_item_id,
        strength=validated.strength,
        rationale=validated.rationale,
        quote=validated.framework_item_quote,
        confidence=proposal.confidence,
        proposed_by_llm_call_id=proposal.llm_call_id,
        confirmed_by=actor_id,
        confirmed_at=datetime.now(UTC),
    ))
    await session.flush()


async def lock_control_writes(session: AsyncSession) -> None:
    # 把判重与编号分配（含人工编辑）在进程之间串起来。
    # 事务级咨询锁活得比这个辅助函数长，由 commit/rollback 释放。
    await session.execute(text("SELECT pg_advisory_xact_lock(734004, 1)"))


def _normalise_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


def normalise_statement(statement: str) -> str:
    """判重用的正文归一化：折叠空白 + 转小写。

    用 `lower()` 而不是 `casefold()`，是为了和 `sql_normalised_statement()` 逐字对齐——
    过滤在 SQL 里做、比对在 Python 里做，两边规则一旦分叉，后果是**静默的**：
    队列该标的没标，页面看起来一切正常。tests/test_review_duplicates.py 守着这一致性。
    """
    return " ".join((statement or "").split()).lower()


def sql_normalised_statement() -> ColumnElement[str]:
    """`normalise_statement` 的 SQL 版本，用来把比对压进一次查询。"""
    return func.lower(func.btrim(func.regexp_replace(Control.statement, r"\s+", " ", "g")))


async def find_statement_duplicate(session: AsyncSession, statement: str) -> Control | None:
    """返回正文与之逐字相同的**已生效**控制点。

    这是个**可证的事实**，不是判断——所以摆给审核者看，而不是替他们并掉。
    """
    normalised = normalise_statement(statement)
    if not normalised:
        return None
    return await session.scalar(
        select(Control)
        .where(sql_normalised_statement() == normalised, Control.status == "active")
        .order_by(Control.id)
        .limit(1)
    )


async def _matrix_origin(session: AsyncSession, proposal: Proposal, data: dict[str, Any]) -> bool:
    original = proposal.payload
    if original.get("origin") != "matrix":
        if any(key in data for key in _PROVENANCE):
            raise AppError("不能将普通抽取改为 Excel 来源", code="matrix.origin_change_forbidden")
        return False
    if any(data.get(key) != original.get(key) for key in _PROVENANCE):
        raise AppError("Excel 来源信息不可修改", code="matrix.provenance_immutable")
    mapping_id = original.get("matrix_mapping_proposal_id")
    if type(mapping_id) is not int:
        raise AppError("无效的列映射来源", code="matrix.mapping_source_invalid")
    mapping = await session.get(Proposal, mapping_id)
    if (
        mapping is None
        or mapping.kind != ProposalKind.MATRIX_MAPPING
        or mapping.status not in (ProposalStatus.ACCEPTED, ProposalStatus.MODIFIED)
        or mapping.payload.get("source_sha256") != original.get("source_sha256")
        or proposal.document_id is not None
        or proposal.llm_call_id is not None
    ):
        raise AppError("Excel 来源未经过确认", code="matrix.source_not_confirmed")
    row_number = original.get("row_number")
    source_rows = mapping.payload.get("source_rows")
    if (
        type(row_number) is not int
        or type(source_rows) is not int
        or not 1 <= row_number <= source_rows
    ):
        raise AppError("Excel 行号无效", code="matrix.row_number_invalid")
    imported = await session.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == "matrix.import",
            AuditLog.entity_type == "Proposal",
            AuditLog.entity_id == str(mapping_id),
        )
        .limit(1)
    )
    if imported is None or proposal.id not in (imported.after or {}).get("proposal_ids", []):
        raise AppError("缺少 Excel 导入记录", code="matrix.import_record_missing")
    return True


async def materialize(
    session: AsyncSession,
    proposal: Proposal,
    data: dict[str, Any],
    *,
    actor_id: int | None,
    merge_into_control_id: int | None = None,
) -> None:
    if not isinstance(data, dict):
        raise AppError("提案内容必须是对象", code="proposal.payload_not_object")
    if merge_into_control_id is not None and proposal.kind != ProposalKind.CONTROL_EXTRACT:
        raise AppError("只有控制点提案可以并入已有控制点", code="proposal.merge_kind_invalid")
    if proposal.kind == ProposalKind.MATRIX_MAPPING:
        from app.matrix.importer import validate_mapping_proposal
        from app.matrix.template import SheetRejected

        try:
            validate_mapping_proposal(proposal, data)
        except SheetRejected as exc:
            raise AppError(str(exc), code=exc.code, params=exc.params) from exc
        except ValueError as exc:
            raise AppError(str(exc), code="matrix.mapping_rejected", params={"detail": str(exc)}) from exc
        for key in ("source_sha256", "source_headers", "source_rows"):
            data[key] = proposal.payload[key]
        return
    if proposal.kind == ProposalKind.MAPPING:
        await _materialize_mapping(session, proposal, data, actor_id=actor_id)
        return
    if proposal.kind == ProposalKind.RELATION:
        await _materialize_relation(session, proposal, data, actor_id=actor_id)
        return
    if proposal.kind == ProposalKind.CONFLICT:
        await _materialize_conflict(session, proposal, data, actor_id=actor_id)
        return
    if proposal.kind == ProposalKind.ANSWER:
        from app.audit.citations import AnswerCitationValidator
        from app.audit.models import AnswerDraft, AuditQuestion, QuestionStatus
        from app.audit.schemas import AnswerProposalPayload
        from app.clauses.models import Clause
        from app.evidence.models import EvidenceItem

        try:
            validated = AnswerProposalPayload.model_validate(data)
        except ValidationError as exc:
            raise AppError(f"审计答复内容无效：{exc}", code="answer.payload_invalid", params={"detail": describe_validation(exc.errors())}) from exc
        question = await session.get(AuditQuestion, validated.question_id)
        if question is None:
            raise NotFound("审计问题不存在", code="audit.question_not_found")
        if await session.scalar(
            select(AnswerDraft.id).where(AnswerDraft.question_id == question.id)
        ) is not None:
            raise Conflict("该问题已有答复草稿", code="audit.response_draft_exists")
        clause_ids = {citation.clause_id for citation in validated.citations}
        known_clauses = set(
            await session.scalars(select(Clause.id).where(Clause.id.in_(clause_ids)))
        )
        known_controls = set(
            await session.scalars(
                select(Control.id).where(Control.id.in_(validated.cited_control_ids))
            )
        )
        known_evidence = set(
            await session.scalars(
                select(EvidenceItem.id).where(
                    EvidenceItem.id.in_(validated.suggested_evidence_ids)
                )
            )
        )
        if clause_ids != known_clauses:
            raise AppError("答复引用了不存在的条款", code="answer.clause_missing")
        if set(validated.cited_control_ids) != known_controls:
            raise AppError("答复引用了不存在的控制点", code="answer.control_missing")
        if set(validated.suggested_evidence_ids) != known_evidence:
            raise AppError("答复引用了不存在的证据", code="answer.evidence_missing")
        reason = await AnswerCitationValidator(
            session,
            allowed_clause_ids=clause_ids,
            allowed_control_ids=known_controls,
            allowed_evidence_ids=known_evidence,
        ).check(data)
        if reason:
            raise AppError(f"引用校验失败：{reason}", code="citation.check_failed", params={"reason": reason})
        session.add(AnswerDraft(
            question_id=question.id,
            body=validated.body,
            language=validated.language,
            cited_clause_ids=sorted(clause_ids),
            cited_control_ids=validated.cited_control_ids,
            suggested_evidence_ids=validated.suggested_evidence_ids,
            gap_notes=validated.gap_notes,
            confidence=proposal.confidence,
            generated_by_llm_call_id=proposal.llm_call_id,
            reviewed_by=actor_id,
        ))
        question.status = QuestionStatus.DRAFTED
        await session.flush()
        return
    if proposal.kind != ProposalKind.CONTROL_EXTRACT:
        raise AppError(
            f"尚不支持确认 {proposal.kind.value} 提案",
            code="proposal.kind_unsupported",
            params={"kind": proposal.kind.value},
        )

    matrix = await _matrix_origin(session, proposal, data)
    try:
        validated = (MatrixControlPayload if matrix else ControlPayload).model_validate(data)
    except ValidationError as exc:
        raise AppError(f"控制点内容无效：{exc}", code="control.payload_invalid", params={"detail": describe_validation(exc.errors())}) from exc
    if matrix:
        if validated.citations:
            raise AppError("Excel 行不得声明条款引用", code="matrix.citation_forbidden")
    else:
        from app.extraction.citations import ClauseCitationValidator

        if not validated.citations:
            raise AppError("控制点必须提供条款引用", code="control.citation_required")
        reason = await ClauseCitationValidator(session, document_id=proposal.document_id).check(
            {"controls": [data]}
        )
        if reason:
            raise AppError(f"引用校验失败：{reason}", code="citation.check_failed", params={"reason": reason})

    await lock_control_writes(session)
    if merge_into_control_id is not None:
        if matrix:
            raise AppError("Excel 导入的控制点不走合并", code="matrix.merge_forbidden")
        await _merge_into(
            session, merge_into_control_id, validated, proposal, actor_id=actor_id
        )
        return

    # 判重只看生效行：命中已合并的会把新提案挂到一条死控制点上。
    live = list(await session.scalars(active_controls().order_by(Control.id)))
    if matrix:
        control = next((c for c in live if c.code == validated.code), None)
        if control is not None and (
            _normalise_title(control.title) != _normalise_title(validated.title)
            or control.statement != validated.statement
            or control.category != validated.category
        ):
            raise Conflict("已有控制点编号对应不同内容", code="control.code_conflict")
    else:
        control = next(
            (c for c in live if _normalise_title(c.title) == _normalise_title(validated.title)),
            None,
        )
    if control is None:
        # 编号分配看全部行，含已合并的——否则 C-0002 会被发第二次。
        allocated = list(await session.scalars(select(Control).order_by(Control.id)))
        maximum = max(
            (int(c.code[2:]) for c in allocated if re.fullmatch(r"C-\d+", c.code)),
            default=0,
        )
        control = Control(
            code=(validated.code if matrix else None) or f"C-{maximum + 1:04d}",
            title=validated.title,
            statement=validated.statement,
            category=validated.category,
        )
        session.add(control)
        await session.flush()

    await _attach_sources(session, control, validated, proposal, actor_id=actor_id)


async def _attach_sources(
    session: AsyncSession,
    control: Control,
    validated: Any,
    proposal: Proposal,
    *,
    actor_id: int | None,
) -> None:
    existing = set(
        await session.scalars(
            select(ControlSource.clause_id).where(ControlSource.control_id == control.id)
        )
    )
    for citation in validated.citations:
        if citation.clause_id in existing:
            continue
        session.add(
            ControlSource(
                control_id=control.id,
                clause_id=citation.clause_id,
                relation=citation.relation,
                confidence=proposal.confidence,
                proposed_by_llm_call_id=proposal.llm_call_id,
                confirmed_by=actor_id,
                confirmed_at=datetime.now(UTC),
            )
        )
        existing.add(citation.clause_id)
    await session.flush()


async def _merge_into(
    session: AsyncSession,
    control_id: int,
    validated: Any,
    proposal: Proposal,
    *,
    actor_id: int | None,
) -> None:
    """审核者选择「并入已有控制点」：只把引用挂过去，不改那条控制点的正文。

    队列可能是几分钟前渲染的，那条控制点这期间可能已被人编辑过——所以这里
    **再验一次正文相同**，而不是信前端送来的 id。赢家保留自己的标题与正文，
    与既有的「标题相同则并入」一条路走法一致。
    """
    control = await session.get(Control, control_id)
    if control is None:
        raise NotFound("要并入的控制点不存在", code="control.merge_target_not_found")
    if control.status != "active":
        raise AppError("要并入的控制点已不再生效", code="control.merge_target_inactive")
    if normalise_statement(control.statement) != normalise_statement(validated.statement):
        raise AppError(
            f"{control.code} 的正文已与本提案不同，无法并入；请刷新确认队列",
            code="control.merge_statement_drifted",
            params={"code": control.code},
        )
    await _attach_sources(session, control, validated, proposal, actor_id=actor_id)


async def update_control(
    session: AsyncSession, control_id: int, changes: dict[str, Any], *, actor: User
) -> Control:
    """人工显式 PATCH；留审计，且所有正式写入都留在本模块内（铁律 1、2）。"""
    from app.controls.schemas import ControlUpdateIn
    from app.iam.audit import record
    from app.iam.permissions import Permission
    from app.review.service import require_actor

    require_actor(actor, Permission.CONTROL_WRITE)
    try:
        parsed = ControlUpdateIn.model_validate(changes)
    except ValidationError as exc:
        raise AppError(f"控制点修改无效：{exc}", code="control.update_invalid", params={"detail": describe_validation(exc.errors())}) from exc
    changes = parsed.model_dump(exclude_unset=True, exclude={"expected"})
    await lock_control_writes(session)
    control = await session.scalar(
        select(Control)
        .where(Control.id == control_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if control is None:
        raise NotFound("控制点不存在", code="control.not_found")
    # 两人同时编辑：后保存的原来直接覆盖先保存的。比对编辑人打开编辑时看到的值，
    # 库里已经变了就拒绝——审计里两次都有记录，但后保存的人并不知道自己抹掉了什么。
    stale = sorted(
        field for field in parsed.expected.model_fields_set
        if getattr(parsed.expected, field) != getattr(control, field)
    )
    if stale:
        raise Conflict(
            f"控制点在你打开编辑之后被修改过：{'、'.join(stale)}",
            code="control.changed_since_loaded",
            params={"fields": stale},
        )
    if changes.get("owner_user_id") is not None:
        owner = await session.get(User, changes["owner_user_id"])
        if owner is None:
            raise AppError("负责人不存在", code="user.owner_not_found")
    before = {field: getattr(control, field) for field in changes}
    for field, value in changes.items():
        setattr(control, field, value)
    await session.flush()
    await record(
        session,
        user=actor,
        action="control.update",
        entity_type="Control",
        entity_id=control.id,
        before=before,
        after={field: getattr(control, field) for field in changes},
    )
    await session.flush()
    return control


async def merge_controls(
    session: AsyncSession,
    *,
    loser_id: int,
    winner_id: int,
    actor: User,
    expected_digest: str | None = None,
) -> MergePlan:
    """把输家的关联转挂到赢家；输家留行，只改标记。不改赢家的标题/正文/分类/负责人。"""
    from dataclasses import asdict

    from app.controls.merge import plan_digest, plan_merge
    from app.environment.models import Implementation
    from app.evidence.models import EvidenceItem
    from app.iam.audit import record
    from app.iam.permissions import Permission
    from app.relations.models import ControlEmbedding
    from app.review.service import require_actor
    from app.risk.models import RiskEntry

    require_actor(actor, Permission.CONTROL_WRITE)
    await lock_control_writes(session)
    # 预览与执行之间库可能已经变了，必须重算，不信客户端送来的计划。
    plan = await plan_merge(session, loser_id=loser_id, winner_id=winner_id)
    # 每种阻断各有一条字面量 code：把原因句子塞进参数里，英文界面就只能看到中文。
    # 预览已经禁用了确认按钮，走到这里多半是预览之后库变了。
    blocked = {blocker.code for blocker in plan.blockers}
    if "merge.blocked.not_found" in blocked:
        raise NotFound("控制点不存在", code="control.not_found")
    if "merge.blocked.self" in blocked:
        raise AppError("不能把控制点并入它自己", code="control.merge_into_self")
    if "merge.blocked.already_merged" in blocked:
        raise AppError("已经合并的控制点不能再合并", code="control.merge_already_merged")
    # 接口要求带上预览时的指纹；None 只留给直接调用的测试与脚本。
    if expected_digest is not None and plan_digest(plan) != expected_digest:
        raise Conflict(
            "预览之后数据有变化，合并要丢弃的内容与预览不同，请重新预览",
            code="control.merge_plan_changed",
        )

    loser = await session.get(Control, loser_id)
    winner = await session.get(Control, winner_id)
    if loser is None or winner is None:
        raise AppError("控制点不存在", code="control.not_found")

    from app.rcsa.models import RcsaItem

    # 先锁实施记录，避免范围添加与控制合并并发后改变评估对象。
    implementation_ids = list(await session.scalars(select(Implementation.id)
        .where(Implementation.control_id == loser_id).order_by(Implementation.id).with_for_update()))
    if implementation_ids and await session.scalar(select(RcsaItem.id)
            .where(RcsaItem.implementation_id.in_(implementation_ids)).limit(1)):
        raise Conflict("实施记录已被 RCSA 引用，不能合并控制", code="rcsa.implementation_in_use")

    before = {
        "code": loser.code,
        "title": loser.title,
        "statement": loser.statement,
        "status": loser.status,
    }

    discard_models = {
        "control_embeddings": ControlEmbedding,
        "control_relations": ControlRelation,
        "control_sources": ControlSource,
        "evidence_items": EvidenceItem,
        "implementations": Implementation,
        "mappings": Mapping,
        "risk_entries": RiskEntry,
    }
    # 先删重复行再转挂，否则 mappings / control_sources 等唯一约束会先炸。
    for discarded in plan.discards:
        row = await session.get(discard_models[discarded.table], discarded.id)
        if row is not None:
            await session.delete(row)
    await session.flush()

    for model in (
        ControlEmbedding,
        ControlSource,
        EvidenceItem,
        Implementation,
        Mapping,
        RiskEntry,
    ):
        for row in list(await session.scalars(select(model).where(model.control_id == loser.id))):
            row.control_id = winner.id

    for row in list(
        await session.scalars(
            select(ControlRelation).where(
                or_(
                    ControlRelation.from_control_id == loser.id,
                    ControlRelation.to_control_id == loser.id,
                )
            )
        )
    ):
        if row.from_control_id == loser.id:
            row.from_control_id = winner.id
        if row.to_control_id == loser.id:
            row.to_control_id = winner.id

    loser.status = "merged"
    loser.merged_into_id = winner.id

    await record(
        session,
        user=actor,
        action="control.merge",
        entity_type="Control",
        entity_id=loser.id,
        before=before,
        after={
            "merged_into": winner.code,
            "moves": plan.moves,
            "discards": [asdict(item) for item in plan.discards],
        },
    )
    await session.flush()
    return plan


async def apply_rcsa_prefill(
    session: AsyncSession, proposal_id: int, *, fields: list[str], expected_version: int, actor: User
):
    """把作答人选定的 RCSA 预填建议写进仍可编辑的草稿。

    只写草稿的回答字段：不提交、不批准、不碰控制库。建议基于的草稿版本与当前不同就拒绝
    （stale），通道已关闭也拒绝。哪个字段来自哪条建议，记进审计。
    """
    from app.rcsa.access import audit, bump, check_version
    from app.rcsa.ai import channel_settings
    from app.rcsa.models import RcsaResponse, ResponseStatus
    from app.rcsa.responses import locked_item, require_assignee
    from app.rcsa.schemas import AnswerV1
    from app.review.service import locked_proposal

    proposal = await locked_proposal(session, proposal_id)
    if proposal is None or proposal.kind != ProposalKind.RCSA_PREFILL:
        raise NotFound("建议不存在", code="rcsa.suggestion_not_found")
    if proposal.status != ProposalStatus.PENDING:
        raise Conflict("这条建议已经用过了", code="rcsa.suggestion_used")
    payload = proposal.payload
    response = await session.get(RcsaResponse, payload["response_id"])
    _, item = await locked_item(session, response.item_id)
    response = await session.scalar(
        select(RcsaResponse).where(RcsaResponse.id == response.id).with_for_update()
        .execution_options(populate_existing=True)
    )
    require_assignee(item, actor)
    if response.status != ResponseStatus.DRAFT:
        raise Conflict("已提交的版本不能修改", code="rcsa.response_not_draft")
    check_version(response, expected_version)
    if response.version != payload["response_version"]:
        raise Conflict("草稿在请求建议之后改过，这条建议已过期", code="rcsa.suggestion_stale")
    if not (await channel_settings(session, "prefill"))["enabled"]:
        raise Conflict("这个 AI 通道没有启用", code="rcsa.ai_disabled", params={"channel": "prefill"})

    answer = dict(response.answer)
    suggested = payload["suggestions"]
    for field in fields:
        if field == "limitations":
            answer["limitations"] = suggested["limitations"]
        else:
            answer[field] = {"rating": suggested[field]["rating"], "rationale": suggested[field]["rationale"]}
    response.answer = AnswerV1.model_validate(answer).model_dump(mode="json")
    bump(response, actor)
    every = {"design", "operating", "limitations"} <= set(fields)
    proposal.status = ProposalStatus.ACCEPTED if every else ProposalStatus.MODIFIED
    proposal.decided_by = actor.id
    proposal.decided_at = datetime.now(UTC)
    proposal.decided_payload = {"fields": fields}
    await session.flush()
    await audit(session, actor, "ai_apply", "RcsaResponse", response.id,
                {"proposal_id": proposal.id, "fields": fields, "response_id": response.id})
    return response
