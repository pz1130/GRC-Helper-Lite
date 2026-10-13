"""第一阶段要求清单；与最终控制点分开留存，不写进脱敏统计。"""

from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin


class ExtractionInventory(TimestampMixin, Base):
    __tablename__ = "extraction_inventories"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    llm_call_id: Mapped[int] = mapped_column(ForeignKey("llm_call.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    normalized_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    normalization_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_call.id"), nullable=True)
    semantic_review: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    review_call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_call.id"), nullable=True)
