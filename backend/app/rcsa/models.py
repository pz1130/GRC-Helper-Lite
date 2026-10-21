"""RCSA（风险与控制自评估）的业务记录与指标观测。

实现合同里落在数据库这一层的部分写在这里，服务层的状态转换与身份规则在 PR-2。

**数据库兜底，不只靠服务层**：评分越界、重复评估项/版本、跨评估项的当前版本指针、
复核目标不唯一或决定与目标不配、作答人兼复核人、行动责任人兼验证人，都由约束拒绝。
另有四个触发器（只在迁移里，`0034_rcsa.py`）：已提交响应的内容冻结、提交后不能再
增删证据、复核记录只增不改、已关闭/已取消活动整行冻结。服务层写错了也落不了库。

**快照与修改规则**：控制/映射/来源快照在激活时冻结；回答、证据在提交时冻结；风险
评分随提交复制进 `risk_snapshot`。关闭后能变化的只有「当前跟进」（行动状态、接受
到期），它们在 item_risks / actions 上；本期评分、回答与 `closed_snapshot` 不原地改。

**身份分离**（谁的 id 从哪个字段取）：作答/提交人 = items.assignee_id 与
responses.submitted_by；复核人 = items.reviewer_id 与 reviews.reviewer_id；处置提议人 =
item_risks.disposition_proposed_by，批准人 = 对应 review 的 reviewer_id；行动责任人 =
actions.owner_id，验证人 = actions.verifier_id；触发提议人 = triggers.proposed_by，
确认人 = 对应 review 的 reviewer_id。表内能比较的两两不同由 CHECK 保证，跨表的
（复核人 ≠ 提交人、批准人 ≠ 提议人）由服务层在锁内检查。

**幂等**：命令键 = actor + 命令类型 + 目标 + 客户端键，连同 request_hash 与结果 id
写进那次操作的审计事件 `after`；`audit_log` 上有只覆盖 `rcsa.*` 的唯一表达式索引，
旧日志不受影响。风险发布另由 risk_entries 上的部分唯一索引保证一个情景只建一条。

**锁顺序**：campaign → item → risk/action，子项写入同样先锁活动并重查状态。

所有外键 RESTRICT：被引用的现有实体要删除时由业务层返回 409，不让 CASCADE 顺带删掉
RCSA 记录。人员停用不删行，历史责任仍可解析。
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin


def _enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
        create_constraint=True,
    )


class ProfileStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class ScoringAlgorithm(StrEnum):
    PRODUCT = "product"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    IN_REVIEW = "in_review"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class ItemStatus(StrEnum):
    PENDING = "pending"
    DRAFT = "draft"
    SUBMITTED = "submitted"
    RETURNED = "returned"
    APPROVED = "approved"
    EXCLUDED = "excluded"


class ResponseStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    RETURNED = "returned"


class ProviderType(StrEnum):
    # D10：留第三方分类位，首版激活只允许 internal。
    INTERNAL = "internal"
    THIRD_PARTY = "third_party"


class EvidencePurpose(StrEnum):
    SELF_ASSESSMENT = "self_assessment"
    INDEPENDENT_TEST = "independent_test"


class Disposition(StrEnum):
    ACCEPT = "accept"
    REDUCE = "reduce"
    TRANSFER = "transfer"
    AVOID = "avoid"
    TERMINATE = "terminate"


class DispositionStatus(StrEnum):
    PENDING = "pending"
    PROPOSED = "proposed"
    APPROVED = "approved"
    RETURNED = "returned"


class ActionStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"
    CANCELLED = "cancelled"


class TriggerType(StrEnum):
    # planned_change 是事前评估，incident 是事后重评，报告里要分得开。
    PLANNED_CHANGE = "planned_change"
    INCIDENT = "incident"
    OTHER = "other"
    KRI = "kri"


class TriggerStatus(StrEnum):
    REGISTERED = "registered"
    TRIAGED = "triaged"
    AWAITING_ASSESSMENT = "awaiting_assessment"
    NO_REASSESSMENT = "no_reassessment"
    COMPLETED = "completed"


class TriggerDisposition(StrEnum):
    REASSESS = "reassess"
    NO_REASSESSMENT = "no_reassessment"


class CoverageDisposition(StrEnum):
    PENDING = "pending"
    ASSESS = "assess"
    EXCLUDED = "excluded"


class ReviewDecision(StrEnum):
    # 响应
    APPROVE = "approve"
    RETURN = "return"
    # 评估项
    EXCLUDE = "exclude"
    # 风险处置
    APPROVE_DISPOSITION = "approve_disposition"
    RETURN_DISPOSITION = "return_disposition"
    # 行动
    VERIFY_PASS = "verify_pass"
    VERIFY_FAIL = "verify_fail"
    APPROVE_CANCELLATION = "approve_cancellation"
    # 触发
    APPROVE_NO_REASSESSMENT = "approve_no_reassessment"
    REJECT_NO_REASSESSMENT = "reject_no_reassessment"


class _Audited(TimestampMixin):
    """可变行：乐观锁版本号 + 谁建、谁最后改。"""

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class RcsaScoringProfile(Base, _Audited):
    __tablename__ = "rcsa_scoring_profiles"
    __table_args__ = (
        UniqueConstraint("key", "revision", name="uq_rcsa_profile_revision"),
        CheckConstraint(
            "status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL"
            " AND length(trim(horizon)) > 0)",
            name="ck_rcsa_profile_approved",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[ScoringAlgorithm] = mapped_column(
        _enum(ScoringAlgorithm, "rcsa_scoring_algorithm"), nullable=False
    )
    # 可能性/影响各档的标签与判断标准、影响维度；版本化，批准后不可改（服务层）。
    definitions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    bands: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    horizon: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[ProfileStatus] = mapped_column(
        _enum(ProfileStatus, "rcsa_profile_status"), nullable=False
    )
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RcsaCampaign(Base, _Audited):
    __tablename__ = "rcsa_campaigns"
    __table_args__ = (
        CheckConstraint("period_start <= period_end", name="ck_rcsa_campaign_period"),
        CheckConstraint(
            "status IN ('draft', 'cancelled')"
            " OR (framework_snapshot IS NOT NULL AND profile_snapshot IS NOT NULL)",
            name="ck_rcsa_campaign_frozen",
        ),
        CheckConstraint(
            "status <> 'closed' OR (closed_snapshot IS NOT NULL AND closed_by IS NOT NULL"
            " AND closed_at IS NOT NULL)",
            name="ck_rcsa_campaign_closed",
        ),
        CheckConstraint(
            "status <> 'cancelled' OR length(trim(status_reason)) > 0",
            name="ck_rcsa_campaign_cancel_reason",
        ),
        Index("ix_rcsa_campaigns_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    framework_id: Mapped[int] = mapped_column(
        ForeignKey("frameworks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    framework_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("rcsa_scoring_profiles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    profile_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    due_at: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[CampaignStatus] = mapped_column(
        _enum(CampaignStatus, "rcsa_campaign_status"), nullable=False
    )
    # 取消、退回 active 等转换的理由；完整历史在审计日志。
    status_reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    predecessor_campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("rcsa_campaigns.id", ondelete="RESTRICT"), index=True
    )
    audit_engagement_id: Mapped[int | None] = mapped_column(
        ForeignKey("audit_engagements.id", ondelete="RESTRICT"), index=True
    )
    activated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RcsaItem(Base, _Audited):
    __tablename__ = "rcsa_items"
    __table_args__ = (
        Index("uq_rcsa_item_control", "campaign_id", "control_id", unique=True,
              postgresql_where=text("implementation_id IS NULL")),
        UniqueConstraint("campaign_id", "implementation_id", name="uq_rcsa_item_implementation"),
        ForeignKeyConstraint(["implementation_id", "control_id"],
                             ["implementations.id", "implementations.control_id"],
                             name="fk_rcsa_item_implementation", ondelete="RESTRICT"),
        CheckConstraint(
            "assignee_id IS NULL OR reviewer_id IS NULL OR assignee_id <> reviewer_id",
            name="ck_rcsa_item_reviewer_not_assignee",
        ),
        # 当前版本必须属于本项：复合外键直接拦住跨评估项、跨活动的指针。
        ForeignKeyConstraint(
            ["current_response_id", "id"],
            ["rcsa_responses.id", "rcsa_responses.item_id"],
            name="fk_rcsa_items_current_response",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        Index("ix_rcsa_items_campaign_status", "campaign_id", "status"),
        Index("ix_rcsa_items_assignee_status", "assignee_id", "status"),
        Index("ix_rcsa_items_reviewer_status", "reviewer_id", "status"),
    )

    # id 同时是复合外键的一列，不显式声明的话 SQLAlchemy 不再给它自增。
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("rcsa_campaigns.id", ondelete="RESTRICT"), nullable=False
    )
    control_id: Mapped[int] = mapped_column(
        ForeignKey("controls.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    implementation_id: Mapped[int | None] = mapped_column(Integer)
    implementation_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    control_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    mapping_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    source_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[ItemStatus] = mapped_column(_enum(ItemStatus, "rcsa_item_status"), nullable=False)
    current_response_id: Mapped[int | None] = mapped_column(Integer)
    provider_type: Mapped[ProviderType] = mapped_column(
        _enum(ProviderType, "rcsa_provider_type"),
        nullable=False,
        default=ProviderType.INTERNAL,
        server_default=ProviderType.INTERNAL.value,
    )
    service_ref: Mapped[str | None] = mapped_column(String(300))
    exit_ref: Mapped[str | None] = mapped_column(String(300))


class RcsaResponse(Base, _Audited):
    __tablename__ = "rcsa_responses"
    __table_args__ = (
        UniqueConstraint("item_id", "revision", name="uq_rcsa_response_revision"),
        UniqueConstraint("id", "item_id", name="uq_rcsa_response_item"),
        Index(
            "uq_rcsa_response_one_draft",
            "item_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
        CheckConstraint(
            "status = 'draft' OR (submitted_by IS NOT NULL AND submitted_at IS NOT NULL"
            " AND risk_snapshot IS NOT NULL)",
            name="ck_rcsa_response_submitted",
        ),
        CheckConstraint("answer_schema = 1", name="ck_rcsa_response_answer_schema"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("rcsa_items.id", ondelete="RESTRICT"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ResponseStatus] = mapped_column(
        _enum(ResponseStatus, "rcsa_response_status"), nullable=False
    )
    # answer 的结构由 answer_schema 指明版本，离线重现闭期报告靠它。
    answer_schema: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    submitted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RcsaResponseEvidence(Base):
    __tablename__ = "rcsa_response_evidence"
    __table_args__ = (
        CheckConstraint(
            "evidence_item_id IS NOT NULL OR length(trim(coalesce(external_location, ''))) > 0",
            name="ck_rcsa_evidence_reference",
        ),
        CheckConstraint(
            "purpose <> 'independent_test' OR ("
            "(tester_user_id IS NOT NULL OR length(trim(coalesce(tester_name, ''))) > 0)"
            " AND tested_at IS NOT NULL"
            " AND length(trim(test_scope)) > 0"
            " AND length(trim(conclusion)) > 0"
            " AND length(trim(independence_basis)) > 0)",
            name="ck_rcsa_evidence_independent_test",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    response_id: Mapped[int] = mapped_column(
        ForeignKey("rcsa_responses.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    evidence_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="RESTRICT"), index=True
    )
    # 只是引用位置，服务端不去抓取；带凭证的链接由服务层拒绝。
    external_location: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[EvidencePurpose] = mapped_column(
        _enum(EvidencePurpose, "rcsa_evidence_purpose"), nullable=False
    )
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    tester_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    tester_name: Mapped[str | None] = mapped_column(String(200))
    tested_at: Mapped[date | None] = mapped_column(Date)
    test_scope: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    conclusion: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    independence_basis: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RcsaItemRisk(Base, _Audited):
    __tablename__ = "rcsa_item_risks"
    __table_args__ = (
        UniqueConstraint("item_id", "scenario_key", name="uq_rcsa_item_risk_scenario"),
        CheckConstraint(
            "inherent_likelihood BETWEEN 1 AND 5 AND inherent_impact BETWEEN 1 AND 5"
            " AND inherent_score BETWEEN 1 AND 25",
            name="ck_rcsa_risk_inherent",
        ),
        CheckConstraint(
            "(residual_likelihood IS NULL) = (residual_impact IS NULL)"
            " AND (residual_likelihood IS NULL) = (residual_score IS NULL)",
            name="ck_rcsa_risk_residual_pair",
        ),
        CheckConstraint(
            "residual_likelihood IS NULL OR (residual_likelihood BETWEEN 1 AND 5"
            " AND residual_impact BETWEEN 1 AND 5 AND residual_score BETWEEN 1 AND 25)",
            name="ck_rcsa_risk_residual",
        ),
        CheckConstraint(
            "disposition_status = 'pending' OR (disposition IS NOT NULL"
            " AND disposition_proposed_by IS NOT NULL AND disposition_proposed_at IS NOT NULL)",
            name="ck_rcsa_risk_disposition_proposed",
        ),
        CheckConstraint(
            "disposition IS DISTINCT FROM 'accept' OR disposition_status = 'pending'"
            " OR (accept_until IS NOT NULL AND reassess_at IS NOT NULL)",
            name="ck_rcsa_risk_accept_dates",
        ),
        Index(
            "ix_rcsa_item_risks_accept_until",
            "accept_until",
            postgresql_where=text("disposition = 'accept'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("rcsa_items.id", ondelete="RESTRICT"), nullable=False
    )
    # 跨期比较用的情景身份：同一控制、同一键才可比。
    scenario_key: Mapped[str] = mapped_column(String(64), nullable=False)
    register_risk_id: Mapped[int | None] = mapped_column(
        ForeignKey("risk_entries.id", ondelete="RESTRICT"), index=True
    )
    cause: Mapped[str] = mapped_column(Text, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str] = mapped_column(Text, nullable=False)
    inherent_likelihood: Mapped[int] = mapped_column(Integer, nullable=False)
    inherent_impact: Mapped[int] = mapped_column(Integer, nullable=False)
    inherent_score: Mapped[int] = mapped_column(Integer, nullable=False)
    inherent_rationale: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    residual_likelihood: Mapped[int | None] = mapped_column(Integer)
    residual_impact: Mapped[int | None] = mapped_column(Integer)
    residual_score: Mapped[int | None] = mapped_column(Integer)
    residual_rationale: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    disposition: Mapped[Disposition | None] = mapped_column(_enum(Disposition, "rcsa_disposition"))
    disposition_status: Mapped[DispositionStatus] = mapped_column(
        _enum(DispositionStatus, "rcsa_disposition_status"),
        nullable=False,
        default=DispositionStatus.PENDING,
        server_default=DispositionStatus.PENDING.value,
    )
    # 处置审批有自己的版本：策略变更保留旧批准记录并重新审批。
    disposition_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    disposition_proposed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    disposition_proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accept_until: Mapped[date | None] = mapped_column(Date)
    reassess_at: Mapped[date | None] = mapped_column(Date)
    disposition_details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class RcsaAction(Base, _Audited):
    __tablename__ = "rcsa_actions"
    __table_args__ = (
        CheckConstraint("owner_id <> verifier_id", name="ck_rcsa_action_verifier_not_owner"),
        CheckConstraint(
            "status <> 'verified' OR verified_at IS NOT NULL", name="ck_rcsa_action_verified_at"
        ),
        Index("ix_rcsa_actions_owner_due", "owner_id", "due_at"),
        Index("ix_rcsa_actions_verifier_status", "verifier_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_risk_id: Mapped[int] = mapped_column(
        ForeignKey("rcsa_item_risks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    measure: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    verifier_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    due_at: Mapped[date] = mapped_column(Date, nullable=False)
    success_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ActionStatus] = mapped_column(
        _enum(ActionStatus, "rcsa_action_status"), nullable=False
    )
    implementation_evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    verification_result: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RcsaTrigger(Base, _Audited):
    __tablename__ = "rcsa_triggers"
    __table_args__ = (
        CheckConstraint(
            "trigger_type <> 'planned_change' OR planned_effective_at IS NOT NULL",
            name="ck_rcsa_trigger_planned_date",
        ),
        CheckConstraint(
            "status <> 'awaiting_assessment' OR disposition = 'reassess'",
            name="ck_rcsa_trigger_awaiting",
        ),
        CheckConstraint(
            "status <> 'no_reassessment' OR (disposition = 'no_reassessment'"
            " AND proposed_by IS NOT NULL AND proposed_at IS NOT NULL"
            " AND length(trim(rationale)) > 0)",
            name="ck_rcsa_trigger_no_reassessment",
        ),
        Index("ix_rcsa_triggers_owner_due", "owner_id", "decision_due_at"),
        Index("ix_rcsa_triggers_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger_type: Mapped[TriggerType] = mapped_column(
        _enum(TriggerType, "rcsa_trigger_type"), nullable=False
    )
    origin_kri_id: Mapped[int | None] = mapped_column(ForeignKey("rcsa_kris.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # 变更单号、事件编号之类的引用文字；不是服务端会去请求的 URL。
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision_due_at: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[TriggerStatus] = mapped_column(
        _enum(TriggerStatus, "rcsa_trigger_status"), nullable=False
    )
    disposition: Mapped[TriggerDisposition | None] = mapped_column(
        _enum(TriggerDisposition, "rcsa_trigger_disposition")
    )
    proposed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("rcsa_campaigns.id", ondelete="RESTRICT"), index=True
    )


class RcsaTriggerControl(Base):
    __tablename__ = "rcsa_trigger_controls"
    __table_args__ = (
        ForeignKeyConstraint(["implementation_id", "control_id"],
                             ["implementations.id", "implementations.control_id"],
                             name="fk_rcsa_trigger_implementation", ondelete="RESTRICT"),
        UniqueConstraint("trigger_id", "control_id", name="uq_rcsa_trigger_control"),
        CheckConstraint(
            "coverage_disposition <> 'excluded' OR length(trim(coverage_reason)) > 0",
            name="ck_rcsa_trigger_control_excluded_reason",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger_id: Mapped[int] = mapped_column(
        ForeignKey("rcsa_triggers.id", ondelete="RESTRICT"), nullable=False
    )
    control_id: Mapped[int] = mapped_column(
        ForeignKey("controls.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    implementation_id: Mapped[int | None] = mapped_column(Integer)
    implementation_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    control_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    coverage_disposition: Mapped[CoverageDisposition] = mapped_column(
        _enum(CoverageDisposition, "rcsa_coverage_disposition"),
        nullable=False,
        default=CoverageDisposition.PENDING,
        server_default=CoverageDisposition.PENDING.value,
    )
    coverage_reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


_REVIEW_TARGETS = "num_nonnulls(response_id, item_id, item_risk_id, action_id, trigger_id) = 1"
_REVIEW_DECISION_FITS_TARGET = (
    "(decision IN ('approve', 'return') AND response_id IS NOT NULL)"
    " OR (decision = 'exclude' AND item_id IS NOT NULL)"
    " OR (decision IN ('approve_disposition', 'return_disposition') AND item_risk_id IS NOT NULL)"
    " OR (decision IN ('verify_pass', 'verify_fail', 'approve_cancellation')"
    " AND action_id IS NOT NULL)"
    " OR (decision IN ('approve_no_reassessment', 'reject_no_reassessment')"
    " AND trigger_id IS NOT NULL)"
)
_REVIEW_REASON_REQUIRED = (
    "decision NOT IN ('return', 'exclude', 'return_disposition', 'verify_fail',"
    " 'approve_cancellation', 'reject_no_reassessment') OR length(trim(reason)) > 0"
)


class RcsaReview(Base):
    """复核、处置批准、行动验证、排除、触发确认——追加不修改。"""

    __tablename__ = "rcsa_reviews"
    __table_args__ = (
        CheckConstraint(_REVIEW_TARGETS, name="ck_rcsa_review_one_target"),
        CheckConstraint(_REVIEW_DECISION_FITS_TARGET, name="ck_rcsa_review_decision_target"),
        CheckConstraint(_REVIEW_REASON_REQUIRED, name="ck_rcsa_review_reason"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    response_id: Mapped[int | None] = mapped_column(
        ForeignKey("rcsa_responses.id", ondelete="RESTRICT"), index=True
    )
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("rcsa_items.id", ondelete="RESTRICT"), index=True
    )
    item_risk_id: Mapped[int | None] = mapped_column(
        ForeignKey("rcsa_item_risks.id", ondelete="RESTRICT"), index=True
    )
    action_id: Mapped[int | None] = mapped_column(
        ForeignKey("rcsa_actions.id", ondelete="RESTRICT"), index=True
    )
    trigger_id: Mapped[int | None] = mapped_column(
        ForeignKey("rcsa_triggers.id", ondelete="RESTRICT"), index=True
    )
    decision: Mapped[ReviewDecision] = mapped_column(
        _enum(ReviewDecision, "rcsa_review_decision"), nullable=False
    )
    # 复核绑定的是哪一版：响应的 revision、处置的 disposition_revision、行动/触发的 version。
    target_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RcsaKri(Base, _Audited):
    """口径创建后不覆盖；调整口径须停用旧指标并新建，历史观测保留原口径。"""

    __tablename__ = "rcsa_kris"
    __table_args__ = (
        ForeignKeyConstraint(["implementation_id", "control_id"],
                             ["implementations.id", "implementations.control_id"],
                             name="fk_rcsa_kri_implementation", ondelete="RESTRICT"),
        CheckConstraint("direction IN ('high', 'low')", name="ck_rcsa_kri_direction"),
        CheckConstraint("(direction = 'high' AND amber_threshold < red_threshold) OR "
                        "(direction = 'low' AND amber_threshold > red_threshold)", name="ck_rcsa_kri_thresholds"),
        CheckConstraint("decision_days BETWEEN 1 AND 90", name="ck_rcsa_kri_decision_days"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    unit: Mapped[str] = mapped_column(String(50), nullable=False)
    control_id: Mapped[int] = mapped_column(ForeignKey("controls.id", ondelete="RESTRICT"), nullable=False)
    implementation_id: Mapped[int | None] = mapped_column(Integer)
    target_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    amber_threshold: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    red_threshold: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision_days: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class RcsaKriObservation(Base):
    __tablename__ = "rcsa_kri_observations"
    __table_args__ = (
        UniqueConstraint("kri_id", "external_id", name="uq_rcsa_kri_observation_external"),
        CheckConstraint("band IN ('green', 'amber', 'red')", name="ck_rcsa_kri_observation_band"),
        Index("ix_rcsa_kri_observation_time", "kri_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kri_id: Mapped[int] = mapped_column(ForeignKey("rcsa_kris.id", ondelete="RESTRICT"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    band: Mapped[str] = mapped_column(String(5), nullable=False)
    rule_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    trigger_id: Mapped[int | None] = mapped_column(ForeignKey("rcsa_triggers.id", ondelete="RESTRICT"))
    received_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
