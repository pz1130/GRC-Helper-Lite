from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FrameworkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    name_zh: str
    name_en: str
    version: str
    source: str
    item_count: int
    imported_at: datetime


class FrameworkItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None
    code: str
    title: str
    description: str
    level: int
    order_index: int
    attributes: dict | None


class CoverageOut(BaseModel):
    item_id: int
    code: str
    title: str
    level: int
    parent_id: int | None
    requirements: int
    covered: int
    superseded_only: int = 0


class GapOut(BaseModel):
    item_id: int
    code: str
    title: str
    has_supporting: bool


class SupersededSupportOut(BaseModel):
    item_id: int
    code: str
    title: str
    controls: list[str]
