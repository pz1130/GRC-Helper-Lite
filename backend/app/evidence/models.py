from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EvidenceCadence(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    ANNUAL = "annual"
    AD_HOC = "ad_hoc"


class EvidenceStatus(StrEnum):
    PLANNED = "planned"
    COLLECTED = "collected"
    MISSING = "missing"


EvidenceItemStatus = EvidenceStatus


def _enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
        create_constraint=True,
    )


class EvidenceType(Base):
    __tablename__ = "evidence_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_zh: Mapped[str] = mapped_column(String(200), nullable=False)
    name_en: Mapped[str] = mapped_column(String(200), nullable=False)
    format: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    cadence: Mapped[EvidenceCadence] = mapped_column(
        _enum(EvidenceCadence, "evidence_cadence"), nullable=False
    )
    typical_source: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        CheckConstraint(
            "status <> 'collected' OR last_collected_at IS NOT NULL",
            name="ck_evidence_collected_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evidence_type_id: Mapped[int] = mapped_column(
        # RESTRICT 而不是 CASCADE：删一个证据类型不该把已登记的审计证据一起
        # 带走（实测过，而且连审计都不留）。被引用时后端返 409，见 evidence/router.py。
        ForeignKey("evidence_types.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    control_id: Mapped[int] = mapped_column(
        ForeignKey("controls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tech_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("tech_assets.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    location_hint: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[EvidenceStatus] = mapped_column(
        _enum(EvidenceStatus, "evidence_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
