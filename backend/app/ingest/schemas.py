from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ingest.models import DocStatus, DocType


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    doc_type: DocType
    status: DocStatus
    version: str | None
    owner: str | None
    approver: str | None
    approved_date: date | None
    effective_date: date | None
    review_due_date: date | None
    original_filename: str
    parse_error: str | None
    parse_error_code: str | None = None
    parse_error_params: dict | None = None
    parse_warnings: str | None
    parse_warning_codes: list[dict] | None = None
    ocr_quality_flag: bool
    supersedes_id: int | None
    created_at: datetime


class PlainTextIn(BaseModel):
    text: str = Field(min_length=1)


class DocumentCoverageOut(BaseModel):
    """语料盲区。规范性判定是启发式（正则匹配 shall/must/should），会误报也会漏报。"""

    document_id: int
    title: str
    status: str
    clauses: int
    normative_clauses: int
    controls: int
    proposals: int
    uncovered_normative: int
    never_extracted: bool


class UncoveredClauseOut(BaseModel):
    clause_id: int
    citation_label: str | None
    heading_path: str | None
    text: str
    strong: bool


class DocumentMetaIn(BaseModel):
    """可人工编辑的文档元数据。

    白名单之外一律拒绝：解析产物（条款树、file_hash、file_path）是派生数据，
    改它等于让库里的引用对不上原文。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=500)
    doc_type: DocType | None = None
    owner: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=32)
    effective_date: date | None = None
    review_due_date: date | None = None

    @model_validator(mode="after")
    def valid_patch(self) -> "DocumentMetaIn":
        if not self.model_fields_set:
            raise ValueError("provide at least one field to change")
        for field in ("title", "doc_type"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be empty")
        return self
