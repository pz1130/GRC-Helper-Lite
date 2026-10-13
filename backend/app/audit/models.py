from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin


def _enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
        create_constraint=True,
    )


class AuditType(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    REGULATORY = "regulatory"


class EngagementStatus(StrEnum):
    PREPARING = "preparing"
    ONGOING = "ongoing"
    CLOSED = "closed"


class QuestionStatus(StrEnum):
    PENDING = "pending"
    DRAFTED = "drafted"
    FINALIZED = "finalized"


class AuditEngagement(Base, TimestampMixin):
    __tablename__ = "audit_engagements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    audit_type: Mapped[AuditType] = mapped_column(_enum(AuditType, "audit_type"), nullable=False)
    framework_id: Mapped[int | None] = mapped_column(
        ForeignKey("frameworks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[EngagementStatus] = mapped_column(
        _enum(EngagementStatus, "audit_engagement_status"), nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AuditQuestion(Base):
    __tablename__ = "audit_questions"
    __table_args__ = (UniqueConstraint("engagement_id", "seq", name="uq_audit_question_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    engagement_id: Mapped[int] = mapped_column(
        ForeignKey("audit_engagements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    framework_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("framework_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[QuestionStatus] = mapped_column(
        _enum(QuestionStatus, "audit_question_status"), nullable=False
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AnswerDraft(Base):
    __tablename__ = "answer_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("audit_questions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    cited_clause_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    cited_control_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    suggested_evidence_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    gap_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    generated_by_llm_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_call.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    final_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
