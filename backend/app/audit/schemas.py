from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.audit.models import AuditType, EngagementStatus, QuestionStatus

Text = Annotated[str, Field(strict=True, min_length=1)]


class EngagementCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: Annotated[str, Field(strict=True, min_length=1, max_length=300)]
    audit_type: AuditType
    framework_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    period_start: date | None = None
    period_end: date | None = None
    status: EngagementStatus = EngagementStatus.PREPARING

    @model_validator(mode="after")
    def valid_period(self) -> "EngagementCreateIn":
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("the audit start date cannot be after the end date")
        return self


class EngagementOut(EngagementCreateIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class QuestionsImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: Text
    language: Annotated[str, Field(pattern=r"^(zh|en)$")] = "en"


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    engagement_id: int
    seq: int
    question_text: str
    language: str
    framework_item_id: int | None
    status: QuestionStatus
    imported_at: datetime


class GenerateAnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Annotated[str, Field(pattern=r"^(zh|en)$")]


class AnswerFinalizeIn(BaseModel):
    """定稿人屏幕上的那一版。库里的正文与它不同就拒绝定稿，而不是定下另一版。"""

    model_config = ConfigDict(extra="forbid")
    body: Text


class AnswerUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    body: Text
    # 开始编辑时的那一版；库里已经变了就拒绝，而不是覆盖别人刚存的。
    # 原来是可选的——页面总会带，脚本不带就能悄悄覆盖（K10）。
    expected_body: str


class AnswerCitationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    clause_id: Annotated[int, Field(strict=True, gt=0)]
    quote: Text


class AnswerProposalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question_id: Annotated[int, Field(strict=True, gt=0)]
    body: Text
    language: Annotated[str, Field(pattern=r"^(zh|en)$")]
    citations: list[AnswerCitationIn] = Field(min_length=1)
    cited_control_ids: list[Annotated[int, Field(strict=True, gt=0)]]
    suggested_evidence_ids: list[Annotated[int, Field(strict=True, gt=0)]]
    gap_notes: str
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class AnswerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    question_id: int
    body: str
    language: str
    cited_clause_ids: list[int]
    cited_control_ids: list[int]
    suggested_evidence_ids: list[int]
    gap_notes: str
    confidence: float | None
    generated_by_llm_call_id: int | None
    reviewed_by: int | None
    final_body: str | None
    finalized_at: datetime | None


class HistoryOut(AnswerOut):
    question_text: str
    engagement_name: str


class SimilarHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    answer_id: int
    question_id: int
    question_text: str
    engagement_name: str
    answer: str
    language: str
    finalized_at: datetime
    similarity: float


class PreflightRowOut(BaseModel):
    framework_item_id: int
    code: str
    title: str
    likely_question: str
    readiness: Annotated[str, Field(pattern=r"^(green|yellow|red)$")]
    reason: str
    control_count: int
    implementation_count: int
    evidence_count: int
    valid_evidence_count: int
    expired_evidence_count: int
    evidence_titles: list[str]
    tool_names: list[str]


class PreflightOut(BaseModel):
    engagement_id: int
    summary: dict[str, int]
    rows: list[PreflightRowOut]
