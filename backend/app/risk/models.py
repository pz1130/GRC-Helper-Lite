from datetime import date
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin


class RiskSource(StrEnum):
    GAP = "gap"
    CONFLICT = "conflict"
    MANUAL = "manual"
    AUDIT_FINDING = "audit_finding"


class RiskStatus(StrEnum):
    OPEN = "open"
    MITIGATING = "mitigating"
    ACCEPTED = "accepted"
    CLOSED = "closed"


def _enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda values: [value.value for value in values],
        create_constraint=True,
    )


class RiskEntry(Base, TimestampMixin):
    __tablename__ = "risk_entries"
    __table_args__ = (
        CheckConstraint("likelihood BETWEEN 1 AND 5", name="ck_risk_likelihood"),
        CheckConstraint("impact BETWEEN 1 AND 5", name="ck_risk_impact"),
        CheckConstraint(
            "residual_likelihood IS NULL OR residual_likelihood BETWEEN 1 AND 5",
            name="ck_risk_residual_likelihood",
        ),
        CheckConstraint(
            "residual_impact IS NULL OR residual_impact BETWEEN 1 AND 5",
            name="ck_risk_residual_impact",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[RiskSource] = mapped_column(_enum(RiskSource, "risk_source"), nullable=False)
    source_ref: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    framework_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("framework_items.id", ondelete="SET NULL"), index=True
    )
    control_id: Mapped[int | None] = mapped_column(
        ForeignKey("controls.id", ondelete="SET NULL"), index=True
    )
    likelihood: Mapped[int] = mapped_column(Integer, nullable=False)
    impact: Mapped[int] = mapped_column(Integer, nullable=False)
    inherent_score: Mapped[int] = mapped_column(Integer, nullable=False)
    mitigation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    residual_likelihood: Mapped[int | None] = mapped_column(Integer)
    residual_impact: Mapped[int | None] = mapped_column(Integer)
    residual_score: Mapped[int | None] = mapped_column(Integer)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[RiskStatus] = mapped_column(_enum(RiskStatus, "risk_status"), nullable=False)
