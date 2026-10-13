from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
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


class MappingStrength(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    SUPPORTING = "supporting"


def _enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
        create_constraint=True,
    )


class Framework(Base, TimestampMixin):
    __tablename__ = "frameworks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name_zh: Mapped[str] = mapped_column(String(200), nullable=False)
    name_en: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    imported_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class FrameworkItem(Base):
    __tablename__ = "framework_items"
    __table_args__ = (
        UniqueConstraint("framework_id", "code", name="uq_framework_item_code"),
        Index("ix_framework_item_attributes", "attributes", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    framework_id: Mapped[int] = mapped_column(
        ForeignKey("frameworks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("framework_items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class Mapping(Base):
    __tablename__ = "mappings"
    __table_args__ = (
        UniqueConstraint("control_id", "framework_item_id", name="uq_control_framework_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    control_id: Mapped[int] = mapped_column(
        ForeignKey("controls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    framework_item_id: Mapped[int] = mapped_column(
        ForeignKey("framework_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strength: Mapped[MappingStrength] = mapped_column(
        _enum(MappingStrength, "mapping_strength"), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    quote: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_by_llm_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_call.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
