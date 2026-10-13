"""每份文档的最近一次判断快照；只存概率和指纹，不复制制度原文。"""

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin


class DecisionScan(Base, TimestampMixin):
    __tablename__ = "decision_scans"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    probabilities: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
