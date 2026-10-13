from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.controls.models import RelationType, SourceRelation
from app.environment.schemas import ImplementationOut
from app.evidence.schemas import EvidenceItemOut
from app.frameworks.models import MappingStrength


class ControlOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    title: str
    statement: str
    category: str | None
    owner_user_id: int | None
    status: str
    merged_into_id: int | None
    created_at: datetime


class SourceOut(BaseModel):
    clause_id: int
    document_id: int
    document_title: str
    citation_label: str
    heading_path: str
    relation: SourceRelation


class RelationOut(BaseModel):
    from_control_id: int
    to_control_id: int
    # 两端的编号：页面原来只能显示 #id，读的人得挨个点进去才知道是哪条控制点。
    from_control_code: str | None = None
    to_control_code: str | None = None
    relation_type: RelationType
    rationale: str


class FrameworkMappingOut(BaseModel):
    framework_id: int
    framework_key: str
    framework_name: str
    framework_name_en: str = ""
    framework_item_id: int
    code: str
    title: str
    strength: MappingStrength
    rationale: str
    quote: str
    confidence: float | None


class ControlDetailOut(ControlOut):
    sources: list[SourceOut]
    relations: list[RelationOut]
    mappings: list[FrameworkMappingOut]
    implementations: list[ImplementationOut]
    evidence: list[EvidenceItemOut]


class ControlExpected(BaseModel):
    """编辑人打开编辑时看到的值。只比对这里给出的字段。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    statement: str | None = None
    category: str | None = None
    owner_user_id: int | None = None


class ControlUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: Annotated[str, Field(strict=True, min_length=1, max_length=500)] | None = None
    statement: Annotated[str, Field(strict=True, min_length=1)] | None = None
    category: Annotated[str, Field(strict=True, max_length=100)] | None = None
    owner_user_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    # 编辑人打开编辑时看到的值；库里的值与它不同就拒绝，而不是让后保存的覆盖先保存的。
    # 原来是可选的——页面总会带，脚本不带就能悄悄覆盖别人刚存的（K10）。
    expected: ControlExpected

    @model_validator(mode="after")
    def valid_patch(self) -> "ControlUpdateIn":
        changed = self.model_fields_set - {"expected"}
        if not changed:
            raise ValueError("provide at least one field to change")
        # 只带 `expected: {}` 或不相干的字段，比对就是空的，等于没有保护。
        uncovered = sorted(changed - self.expected.model_fields_set)
        if uncovered:
            raise ValueError(f"expected must include every field being changed: {', '.join(uncovered)}")
        for field in ("title", "statement"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be empty")
        return self


class DiscardOut(BaseModel):
    table: str
    id: int
    # detail 是中文兜底；前端按 code 查 mergeReasons 词条、用 params 组句。
    detail: str
    code: str
    params: dict[str, object]


class BlockerOut(BaseModel):
    code: str
    detail: str


class MergePlanOut(BaseModel):
    loser_code: str
    winner_code: str
    moves: dict[str, int]
    discards: list[DiscardOut]
    blockers: list[BlockerOut]
    # 执行合并时原样带回；与重算出的计划对不上就拒绝，要求重新预览。
    plan_digest: str


class MergeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    into_control_id: Annotated[int, Field(strict=True, gt=0)]
    plan_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
