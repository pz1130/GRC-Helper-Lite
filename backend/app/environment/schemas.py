from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.environment.models import (
    HowEnforced,
    ImplementationStatus,
    TechAssetCategory,
    TechAssetEnvironment,
    TechAssetStatus,
)

NonEmptyText = Annotated[str, Field(strict=True, min_length=1)]


class TechAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: TechAssetCategory
    vendor: str
    environment: TechAssetEnvironment
    owner_user_id: int | None
    scope_note: str
    status: TechAssetStatus
    created_at: datetime


class TechAssetCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Annotated[str, Field(strict=True, min_length=1, max_length=200)]
    category: TechAssetCategory
    vendor: Annotated[str, Field(strict=True, max_length=200)] = ""
    environment: TechAssetEnvironment
    owner_user_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    scope_note: Annotated[str, Field(strict=True)] = ""
    status: TechAssetStatus = TechAssetStatus.ACTIVE


class TechAssetUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: Annotated[str, Field(strict=True, min_length=1, max_length=200)] | None = None
    category: TechAssetCategory | None = None
    vendor: Annotated[str, Field(strict=True, max_length=200)] | None = None
    environment: TechAssetEnvironment | None = None
    owner_user_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    scope_note: Annotated[str, Field(strict=True)] | None = None
    status: TechAssetStatus | None = None

    @model_validator(mode="after")
    def require_change(self) -> "TechAssetUpdateIn":
        if not self.model_fields_set:
            raise ValueError("provide at least one field to change")
        return self


class ImplementationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    control_id: int
    tech_asset_id: int | None
    description: str
    how_enforced: HowEnforced
    status: ImplementationStatus
    na_justification: str | None
    owner_user_id: int | None
    last_verified_at: datetime | None
    control_code: str | None = None
    control_title: str | None = None


class ImplementationCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    control_id: Annotated[int, Field(strict=True, gt=0)]
    tech_asset_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    description: Annotated[str, Field(strict=True)] = ""
    how_enforced: HowEnforced
    status: ImplementationStatus
    na_justification: Annotated[str, Field(strict=True)] | None = None
    owner_user_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    last_verified_at: datetime | None = None

    @model_validator(mode="after")
    def require_na_justification(self) -> "ImplementationCreateIn":
        if self.status is ImplementationStatus.NOT_APPLICABLE and not (
            self.na_justification and self.na_justification.strip()
        ):
            raise ValueError("not_applicable needs na_justification")
        return self


class ImplementationUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tech_asset_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    description: Annotated[str, Field(strict=True)] | None = None
    how_enforced: HowEnforced | None = None
    status: ImplementationStatus | None = None
    na_justification: Annotated[str, Field(strict=True)] | None = None
    owner_user_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    last_verified_at: datetime | None = None

    @model_validator(mode="after")
    def require_change_and_na_justification(self) -> "ImplementationUpdateIn":
        if not self.model_fields_set:
            raise ValueError("provide at least one field to change")
        if self.status is ImplementationStatus.NOT_APPLICABLE and not (
            self.na_justification and self.na_justification.strip()
        ):
            raise ValueError("not_applicable needs na_justification")
        return self
