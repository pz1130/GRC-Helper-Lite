from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evidence.models import EvidenceCadence, EvidenceStatus


class EvidenceTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name_zh: str
    name_en: str
    format: str
    cadence: EvidenceCadence
    typical_source: str
    description: str


class EvidenceTypeCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # 产品只有英文界面（2026-09-23 起）：中文名不再必填，旧数据里有就留着。
    name_zh: Annotated[str, Field(strict=True, max_length=200)] = ""
    name_en: Annotated[str, Field(strict=True, min_length=1, max_length=200)]
    format: Annotated[str, Field(strict=True, max_length=64)] = ""
    cadence: EvidenceCadence
    typical_source: Annotated[str, Field(strict=True, max_length=500)] = ""
    description: Annotated[str, Field(strict=True)] = ""


class EvidenceTypeUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name_zh: Annotated[str, Field(strict=True, max_length=200)] | None = None
    name_en: Annotated[str, Field(strict=True, min_length=1, max_length=200)] | None = None
    format: Annotated[str, Field(strict=True, max_length=64)] | None = None
    cadence: EvidenceCadence | None = None
    typical_source: Annotated[str, Field(strict=True, max_length=500)] | None = None
    description: Annotated[str, Field(strict=True)] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "EvidenceTypeUpdateIn":
        if not self.model_fields_set:
            raise ValueError("provide at least one field to change")
        return self


class EvidenceItemOut(BaseModel):
    id: int
    evidence_type_id: int
    control_id: int
    tech_asset_id: int | None
    title: str
    owner_user_id: int | None
    location_hint: str
    last_collected_at: datetime | None
    valid_until: datetime | None
    file_path: str | None
    # status 是当下的展示状态；intent_status 保留人工登记时的原意。
    status: str
    intent_status: EvidenceStatus
    display_status: str
    created_at: datetime
    evidence_type_name: str | None = None
    control_code: str | None = None
    control_title: str | None = None
    tech_asset_name: str | None = None


class EvidenceItemCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_type_id: Annotated[int, Field(strict=True, gt=0)]
    control_id: Annotated[int, Field(strict=True, gt=0)]
    tech_asset_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    title: Annotated[str, Field(strict=True, min_length=1, max_length=500)]
    owner_user_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    location_hint: Annotated[str, Field(strict=True)] = ""
    last_collected_at: datetime | None = None
    valid_until: datetime | None = None
    file_path: Annotated[str, Field(strict=True, max_length=1000)] | None = None
    status: EvidenceStatus

    @model_validator(mode="after")
    def require_collection_timestamp(self) -> "EvidenceItemCreateIn":
        if self.status is EvidenceStatus.COLLECTED and self.last_collected_at is None:
            raise ValueError("collected evidence needs last_collected_at")
        return self


class EvidenceItemUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_type_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    control_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    tech_asset_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    title: Annotated[str, Field(strict=True, min_length=1, max_length=500)] | None = None
    owner_user_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    location_hint: Annotated[str, Field(strict=True)] | None = None
    last_collected_at: datetime | None = None
    valid_until: datetime | None = None
    file_path: Annotated[str, Field(strict=True, max_length=1000)] | None = None
    status: EvidenceStatus | None = None

    @model_validator(mode="after")
    def require_change_and_collection_timestamp(self) -> "EvidenceItemUpdateIn":
        if not self.model_fields_set:
            raise ValueError("provide at least one field to change")
        if self.status is EvidenceStatus.COLLECTED and self.last_collected_at is None:
            raise ValueError("collected evidence needs last_collected_at")
        return self
