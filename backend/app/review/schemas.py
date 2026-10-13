from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.review.models import ProposalKind, ProposalStatus
from app.review.service import Decision, ReviewTier


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: ProposalKind
    payload: dict[str, Any]
    citations: list[Any]
    confidence: float | None
    document_id: int | None
    status: ProposalStatus
    decided_by: int | None
    decided_at: datetime | None
    decided_payload: dict[str, Any] | None
    reject_reason: str | None
    created_at: datetime
    bulk_acceptable: bool = False
    ocr_quality_flag: bool = False
    # 正文用了 must/shall，而被引原文里一个情态词都没有——结论强于出处。
    normative_drift: bool = False
    extraction_review: dict[str, Any] | None = None
    # 已有某条控制点的正文与本提案逐字相同。是事实不是判断，由审核者当场选并入还是另建。
    duplicate_of: dict[str, Any] | None = None
    mapping_context: dict[str, Any] | None = None
    relation_context: dict[str, Any] | None = None
    review_tier: ReviewTier = ReviewTier.MANUAL
    review_reasons: list[str] = Field(default_factory=list)


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Decision
    payload: dict[str, Any] | None = None
    reason: str | None = None
    # 选了就把引用挂到这条已有控制点上；不选就是原来的行为，另建一条。
    merge_into_control_id: Annotated[int, Field(strict=True, gt=0)] | None = None

    @model_validator(mode="after")
    def _check(self) -> "DecideIn":
        if self.decision is Decision.MODIFY and not self.payload:
            raise ValueError("accepting with changes needs the changed payload")
        if self.decision is Decision.REJECT and not (self.reason or "").strip():
            # 拒绝原因会回流用于改进 prompt，不能省
            raise ValueError("a rejection needs a reason")
        if self.decision is not Decision.MODIFY and self.payload is not None:
            raise ValueError("a payload is allowed only when accepting with changes")
        if self.decision is not Decision.REJECT and self.reason is not None:
            raise ValueError("a reason is allowed only when rejecting")
        if self.decision is Decision.REJECT and self.merge_into_control_id is not None:
            raise ValueError("a rejected proposal cannot be merged into a control")
        return self


class BulkAcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_ids(self) -> "BulkAcceptIn":
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("proposal ids must not repeat")
        return self


class AutoProcessIn(BaseModel):
    """执行只针对预览里给人看过的那张清单。

    不收 id 时曾经重新扫整个队列、接受「此刻合格的那些」：预览之后新进来的、
    或超出预览列表的，都会被自动落库，而没有人看过它们。所以 id 必填。
    """

    model_config = ConfigDict(extra="forbid")
    ids: Annotated[list[int], Field(max_length=200)]
    limit: Annotated[int, Field(strict=True, ge=1, le=200)] = 200
