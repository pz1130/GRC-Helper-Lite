"""RCSA 接口的请求与响应结构。请求一律拒绝多余字段。"""

from datetime import date, datetime
from typing import Annotated, Any
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
