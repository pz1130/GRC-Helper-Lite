from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin


class AssessmentStatus(StrEnum):
    DRAFT = "draft"
    FINAL = "final"


class ScoreSource(StrEnum):
    AI = "ai"
    HUMAN = "human"


def _enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda values: [value.value for value in values],
        create_constraint=True,
    )


class MaturityAssessment(Base, TimestampMixin):
    __tablename__ = "maturity_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    framework_id: Mapped[int] = mapped_column(
        ForeignKey("frameworks.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[AssessmentStatus] = mapped_column(
        _enum(AssessmentStatus, "maturity_assessment_status"), nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class MaturityScore(Base):
    __tablename__ = "maturity_scores"
    __table_args__ = (
        UniqueConstraint("assessment_id", "framework_item_id", name="uq_maturity_score_item"),
        CheckConstraint("doc_score BETWEEN 0 AND 4", name="ck_maturity_doc_score"),
        CheckConstraint("impl_score BETWEEN 0 AND 4", name="ck_maturity_impl_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_id: Mapped[int] = mapped_column(
        ForeignKey("maturity_assessments.id", ondelete="CASCADE"), index=True
    )
    framework_item_id: Mapped[int] = mapped_column(
        ForeignKey("framework_items.id", ondelete="CASCADE"), index=True
    )
    doc_score: Mapped[int] = mapped_column(Integer, nullable=False)
    impl_score: Mapped[int] = mapped_column(Integer, nullable=False)
    doc_rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    impl_rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    doc_score_source: Mapped[ScoreSource] = mapped_column(
        _enum(ScoreSource, "maturity_doc_score_source"), nullable=False
    )
    impl_score_source: Mapped[ScoreSource] = mapped_column(
        _enum(ScoreSource, "maturity_impl_score_source"), nullable=False
    )
    scored_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
