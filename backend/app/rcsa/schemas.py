"""RCSA 接口的请求与响应结构。请求一律拒绝多余字段。"""

from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Id = Annotated[int, Field(strict=True, gt=0)]
Version = Annotated[int, Field(strict=True, ge=1)]
Text = Annotated[str, Field(max_length=20000)]
Short = Annotated[str, Field(max_length=300)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int


class VersionIn(_In):
    expected_version: Version


# ── 评分档案 ────────────────────────────────────────────────


class Band(_In):
    min: Annotated[int, Field(strict=True, ge=1, le=25)]
    max: Annotated[int, Field(strict=True, ge=1, le=25)]
    label: Annotated[str, Field(min_length=1, max_length=50)]


class ProfileCreateIn(_In):
    key: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")]
    # 从哪一版复制口径；不给就从默认 5×5 的初始建议开始。
    base_profile_id: Id | None = None


class ProfileUpdateIn(_In):
    definitions: dict[str, Any] | None = None
    bands: list[Band] | None = None
    horizon: Annotated[str, Field(max_length=200)] | None = None
    expected_version: Version


class ProfileOut(_Out):
    id: int
    key: str
    revision: int
    algorithm: str
    definitions: dict[str, Any]
    bands: list[dict[str, Any]]
    horizon: str
    status: str
    approved_by: int | None
    approved_at: datetime | None
    version: int


# ── 活动 ────────────────────────────────────────────────────


def _valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown time zone") from exc
    return value


class CampaignCreateIn(_In):
    name: Annotated[str, Field(min_length=1, max_length=300)]
    framework_id: Id
    profile_id: Id
    period_start: date
    period_end: date
    due_at: date
    timezone: Annotated[str, Field(min_length=1, max_length=64)] = "UTC"
    owner_id: Id | None = None
    predecessor_campaign_id: Id | None = None
    audit_engagement_id: Id | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        return _valid_timezone(value)

    @model_validator(mode="after")
    def period_is_ordered(self) -> "CampaignCreateIn":
        if self.period_start > self.period_end:
            raise ValueError("period_end must not be before period_start")
        return self


class CampaignUpdateIn(_In):
    name: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    profile_id: Id | None = None
    period_start: date | None = None
    period_end: date | None = None
    due_at: date | None = None
    timezone: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    owner_id: Id | None = None
    audit_engagement_id: Id | None = None
    expected_version: Version

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        return None if value is None else _valid_timezone(value)


class ReasonIn(_In):
    reason: Annotated[str, Field(min_length=1, max_length=5000)]
    expected_version: Version


class CloseIn(_In):
    expected_version: Version
    # 有逾期未完成行动时，活动负责人必须说明为什么仍然关闭。
    overdue_acknowledgement: Annotated[str, Field(max_length=5000)] = ""
    # 提交后才过期的证据：关闭时要明确承认这个限制。
    evidence_acknowledgement: Annotated[str, Field(max_length=5000)] = ""


class CampaignCounts(BaseModel):
    total: int = 0
    submitted: int = 0
    approved: int = 0
    excluded: int = 0


class CampaignOut(_Out):
    id: int
    name: str
    framework_id: int
    framework_snapshot: dict[str, Any] | None
    profile_id: int
    profile_snapshot: dict[str, Any] | None
    period_start: date
    period_end: date
    due_at: date
    timezone: str
    owner_id: int
    status: str
    status_reason: str
    predecessor_campaign_id: int | None
    audit_engagement_id: int | None
    activated_at: datetime | None
    closed_at: datetime | None
    version: int
    counts: CampaignCounts = CampaignCounts()


class ScopeRow(BaseModel):
    control_id: int
    code: str
    title: str
    owner_user_id: int | None
    framework_items: list[str]
    included: bool


# ── 评估项 ──────────────────────────────────────────────────


class ItemsAddIn(_In):
    control_ids: Annotated[list[Id], Field(min_length=1, max_length=200)]
    assignee_id: Id | None = None
    reviewer_id: Id | None = None


class AssignIn(_In):
    assignee_id: Id | None = None
    reviewer_id: Id | None = None
    reason: Annotated[str, Field(max_length=5000)] = ""
    expected_version: Version


class ItemOut(_Out):
    id: int
    campaign_id: int
    control_id: int
    control_snapshot: dict[str, Any] | None
    mapping_snapshot: list[dict[str, Any]] | None
    source_snapshot: list[dict[str, Any]] | None
    assignee_id: int | None
    reviewer_id: int | None
    status: str
    current_response_id: int | None
    provider_type: str
    version: int


# ── 响应（answer v1）────────────────────────────────────────

Effectiveness = Literal["effective", "partially_effective", "ineffective", "not_assessed"]
TestStatus = Literal["provided", "not_provided", "not_applicable"]


class Rating(_In):
    """设计有效性与运行有效性分开评，各带理由；缺证据不等于有效。"""

    rating: Effectiveness | None = None
    rationale: Text = ""


class Followup(_In):
    owner_id: Id
    due_at: date
    description: Annotated[str, Field(min_length=1, max_length=2000)]


class AnswerV1(_In):
    """`rcsa_responses.answer` 的第 1 版结构（answer_schema = 1）。

    草稿可以不完整；完整性在提交时检查，缺什么以 problem code 列出。
    """

    design: Rating = Rating()
    operating: Rating = Rating()
    assessment_period_start: date | None = None
    assessment_period_end: date | None = None
    limitations: Text = ""
    independent_test_status: TestStatus | None = None
    independent_test_reason: Text = ""
    not_assessed_reason: Text = ""
    evidence_followup: Followup | None = None
    no_risk_rationale: Text = ""
    evidence_limitations_acknowledged: bool = False


class AnswerIn(_In):
    answer: AnswerV1
    expected_version: Version


class EvidenceIn(_In):
    evidence_item_id: Id | None = None
    external_location: Annotated[str, Field(max_length=2000)] | None = None
    purpose: Literal["self_assessment", "independent_test"]
    # 外部引用自己报的日期（有就参与有效期检查）与用途/期间覆盖说明。
    collected_at: date | None = None
    valid_until: date | None = None
    coverage_note: Annotated[str, Field(max_length=2000)] = ""
    tester_user_id: Id | None = None
    tester_name: Annotated[str, Field(max_length=200)] | None = None
    tested_at: date | None = None
    test_scope: Annotated[str, Field(max_length=5000)] = ""
    conclusion: Annotated[str, Field(max_length=5000)] = ""
    independence_basis: Annotated[str, Field(max_length=5000)] = ""

    @model_validator(mode="after")
    def reference_and_test_fields(self) -> "EvidenceIn":
        if self.evidence_item_id is None and not (self.external_location or "").strip():
            raise ValueError("give a registered evidence item or an external location")
        if self.purpose == "independent_test" and not (
            (self.tester_user_id or (self.tester_name or "").strip())
            and self.tested_at and self.test_scope and self.conclusion and self.independence_basis
        ):
            raise ValueError(
                "an independent test needs a tester, test date, scope, conclusion and independence basis"
            )
        return self


class EvidenceOut(_Out):
    id: int
    response_id: int
    evidence_item_id: int | None
    external_location: str | None
    purpose: str
    snapshot: dict[str, Any]
    tester_user_id: int | None
    tester_name: str | None
    tested_at: date | None
    test_scope: str
    conclusion: str
    independence_basis: str
    created_at: datetime


class ResponseOut(_Out):
    id: int
    item_id: int
    revision: int
    status: str
    answer_schema: int
    answer: dict[str, Any]
    risk_snapshot: list[dict[str, Any]] | None
    submitted_by: int | None
    submitted_at: datetime | None
    version: int
    evidence: list[EvidenceOut] = []


class ReviewIn(_In):
    decision: Literal["approve", "return"]
    reason: Annotated[str, Field(max_length=5000)] = ""
    expected_version: Version


class ReviewOut(_Out):
    id: int
    response_id: int | None
    item_id: int | None
    item_risk_id: int | None
    action_id: int | None
    trigger_id: int | None
    decision: str
    target_version: int
    reviewer_id: int
    reason: str
    at: datetime


# ── 风险情景、处置与行动 ────────────────────────────────────

Score = Annotated[int, Field(strict=True, ge=1, le=5)]
Slug = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")]
Required = Annotated[str, Field(min_length=1, max_length=5000)]


class RiskIn(_In):
    scenario_key: Slug
    cause: Required
    event: Required
    impact: Required
    inherent_likelihood: Score
    inherent_impact: Score
    inherent_rationale: Text = ""
    residual_likelihood: Score | None = None
    residual_impact: Score | None = None
    residual_rationale: Text = ""
    owner_id: Id

    @model_validator(mode="after")
    def residual_is_a_pair(self) -> "RiskIn":
        if (self.residual_likelihood is None) != (self.residual_impact is None):
            raise ValueError("residual likelihood and residual impact must be given together")
        return self


class RiskUpdateIn(_In):
    cause: Required | None = None
    event: Required | None = None
    impact: Required | None = None
    inherent_likelihood: Score | None = None
    inherent_impact: Score | None = None
    inherent_rationale: Text | None = None
    residual_likelihood: Score | None = None
    residual_impact: Score | None = None
    residual_rationale: Text | None = None
    expected_version: Version

    @model_validator(mode="after")
    def residual_is_a_pair(self) -> "RiskUpdateIn":
        given = self.model_fields_set
        if ("residual_likelihood" in given) != ("residual_impact" in given):
            raise ValueError("residual likelihood and residual impact must be changed together")
        return self


class RiskOwnerIn(_In):
    owner_id: Id
    expected_version: Version


class DispositionIn(_In):
    disposition: Literal["accept", "reduce", "transfer", "avoid", "terminate"]
    details: dict[str, Any] = {}
    accept_until: date | None = None
    reassess_at: date | None = None
    expected_version: Version


class DispositionReviewIn(_In):
    decision: Literal["approve", "return"]
    reason: Annotated[str, Field(max_length=5000)] = ""
    expected_version: Version


class PublishIn(_In):
    mode: Literal["create", "link"]
    register_risk_id: Id | None = None
    expected_version: Version


class RiskOut(_Out):
    id: int
    item_id: int
    scenario_key: str
    register_risk_id: int | None
    cause: str
    event: str
    impact: str
    inherent_likelihood: int
    inherent_impact: int
    inherent_score: int
    inherent_rationale: str
    residual_likelihood: int | None
    residual_impact: int | None
    residual_score: int | None
    residual_rationale: str
    owner_id: int
    disposition: str | None
    disposition_status: str
    disposition_revision: int
    disposition_proposed_by: int | None
    disposition_proposed_at: datetime | None
    accept_until: date | None
    reassess_at: date | None
    disposition_details: dict[str, Any]
    version: int


class ImplementationEvidence(_In):
    description: Annotated[str, Field(min_length=1, max_length=2000)]
    location: Annotated[str, Field(max_length=2000)] = ""


class ActionIn(_In):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    root_cause: Text = ""
    measure: Text = ""
    owner_id: Id
    verifier_id: Id
    due_at: date
    success_criteria: Required


class ActionUpdateIn(_In):
    """负责人更新进度；改负责人、验证人或期限要 RCSA 管理权限并写明理由。"""

    status: Literal["open", "in_progress", "pending_verification"] | None = None
    implementation_evidence: Annotated[list[ImplementationEvidence], Field(max_length=50)] | None = None
    root_cause: Text | None = None
    measure: Text | None = None
    owner_id: Id | None = None
    verifier_id: Id | None = None
    due_at: date | None = None
    reason: Annotated[str, Field(max_length=5000)] = ""
    expected_version: Version


class VerifyIn(_In):
    decision: Literal["pass", "fail"]
    result: Required
    expected_version: Version


class ActionOut(_Out):
    id: int
    item_risk_id: int
    title: str
    root_cause: str
    measure: str
    owner_id: int
    verifier_id: int
    due_at: date
    success_criteria: str
    status: str
    implementation_evidence: list[dict[str, Any]]
    verification_result: str
    verified_at: datetime | None
    version: int


# ── 触发 ────────────────────────────────────────────────────


class TriggerIn(_In):
    trigger_type: Literal["planned_change", "incident", "other"]
    title: Annotated[str, Field(min_length=1, max_length=300)]
    description: Text = ""
    # 变更单号、事件编号之类的引用文字；服务端不会去请求它。
    source_ref: Annotated[str, Field(min_length=1, max_length=500)]
    occurred_at: datetime
    planned_effective_at: datetime | None = None
    owner_id: Id
    decision_due_at: date
    control_ids: Annotated[list[Id], Field(max_length=200)] = []

    @model_validator(mode="after")
    def planned_change_has_a_date(self) -> "TriggerIn":
        if self.trigger_type == "planned_change" and self.planned_effective_at is None:
            raise ValueError("a planned change needs its planned effective date")
        return self


class TriggerControlIn(_In):
    control_id: Id
    coverage_disposition: Literal["pending", "assess", "excluded"] = "pending"
    coverage_reason: Annotated[str, Field(max_length=5000)] = ""


class TriggerUpdateIn(_In):
    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    description: Text | None = None
    source_ref: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    planned_effective_at: datetime | None = None
    owner_id: Id | None = None
    decision_due_at: date | None = None
    controls: Annotated[list[TriggerControlIn], Field(max_length=200)] | None = None
    expected_version: Version


class TriggerDecideIn(_In):
    decision: Literal["reassess", "no_reassessment"]
    rationale: Text = ""
    expected_version: Version


class TriggerExclusion(_In):
    control_id: Id
    reason: Annotated[str, Field(min_length=1, max_length=5000)]


class TriggerLinkIn(_In):
    campaign_id: Id
    exclusions: Annotated[list[TriggerExclusion], Field(max_length=200)] = []
    expected_version: Version


class TriggerReviewIn(_In):
    decision: Literal["approve", "reject"]
    reason: Annotated[str, Field(max_length=5000)] = ""
    expected_version: Version


class TriggerControlOut(_Out):
    id: int
    control_id: int
    control_snapshot: dict[str, Any]
    coverage_disposition: str
    coverage_reason: str


class TriggerOut(_Out):
    id: int
    trigger_type: str
    title: str
    description: str
    source_ref: str
    occurred_at: datetime
    planned_effective_at: datetime | None
    owner_id: int
    decision_due_at: date
    status: str
    disposition: str | None
    proposed_by: int | None
    proposed_at: datetime | None
    rationale: str
    campaign_id: int | None
    version: int
    controls: list[TriggerControlOut] = []


# ── 待办 ────────────────────────────────────────────────────


class WorkRow(BaseModel):
    """一条待办。kind 决定 id 指向什么：评估项、风险情景、行动或触发。"""

    kind: Literal[
        "assessment", "review", "disposition", "approval", "acceptance", "action", "verification",
        "trigger", "trigger_review",
    ]
    id: int
    title: str
    campaign_id: int | None = None
    campaign_name: str | None = None
    status: str
    due_at: date | None
    overdue: bool


class MyWork(BaseModel):
    rows: list[WorkRow]
