from datetime import date
from enum import StrEnum

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, select
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, TimestampMixin


class DocType(StrEnum):
    POLICY = "policy"
    STANDARD = "standard"
    PROCEDURE = "procedure"
    GUIDELINE = "guideline"


class DocStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    PARSE_FAILED = "parse_failed"


def _enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
        create_constraint=True,
    )


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    doc_type: Mapped[DocType] = mapped_column(_enum(DocType, "doc_type"), nullable=False)
    status: Mapped[DocStatus] = mapped_column(
        _enum(DocStatus, "doc_status"), default=DocStatus.UPLOADED, nullable=False
    )

    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approver: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    review_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)

    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)

    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 失败原因的 code 与变量（0027）。parse_error 那句原文只是兜底：老行没有 code。
    parse_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_error_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    parse_warnings: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 与 parse_warnings 并行：同一批告警的 code 与变量，前端据此翻译。
    # NULL = 这行是迁移前存的，或者告警都还没带 code——回落到中文句子。
    parse_warning_codes: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    ocr_quality_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 当前读取快照；NULL 表示旧数据尚未保存独立原文块。
    source_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)

    clauses: Mapped[list["Clause"]] = relationship(  # noqa: F821
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


def superseded_document_ids():
    """已被新版替换的文档 id，作子查询用。

    跨文档的分析（检索、冲突候选、覆盖扫描）拿它排除旧版：旧版的条文不再是
    现行要求，但它的行与出处都留着，按 id 找得到。
    """
    return select(Document.id).where(Document.status == DocStatus.SUPERSEDED)
