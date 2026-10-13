from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.risk.models import RiskSource, RiskStatus

Score = Annotated[int, Field(strict=True, ge=1, le=5)]


class RiskCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: Annotated[str, Field(strict=True, min_length=1, max_length=300)]
    description: str = ""
    likelihood: Score
    impact: Score
    mitigation: str = ""
    residual_likelihood: Score | None = None
    residual_impact: Score | None = None
    owner_user_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    due_date: date | None = None

    @model_validator(mode="after")
    def residual_scores_are_a_pair(self) -> "RiskCreateIn":
        if (self.residual_likelihood is None) != (self.residual_impact is None):
            raise ValueError("residual likelihood and residual impact must be given together")
        return self


class RiskUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: Annotated[str, Field(strict=True, min_length=1, max_length=300)] | None = None
    description: str | None = None
    likelihood: Score | None = None
    impact: Score | None = None
    mitigation: str | None = None
    residual_likelihood: Score | None = None
    residual_impact: Score | None = None
    owner_user_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    due_date: date | None = None
    status: RiskStatus | None = None


class GapRiskCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment_id: Annotated[int, Field(strict=True, gt=0)]
    framework_item_id: Annotated[int, Field(strict=True, gt=0)]


class RiskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    description: str
    source: RiskSource
    source_ref: dict[str, Any]
    framework_item_id: int | None
    control_id: int | None
    likelihood: int
    impact: int
    inherent_score: int
    mitigation: str
    residual_likelihood: int | None
    residual_impact: int | None
    residual_score: int | None
    owner_user_id: int | None
    due_date: date | None
    status: RiskStatus
    created_at: datetime
    updated_at: datetime


class RiskOwnerOut(BaseModel):
    id: int
    name: str
    email: str
