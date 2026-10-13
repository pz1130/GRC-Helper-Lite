from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def normalise_pair(a: int, b: int) -> tuple[int, int]:
    """冲突无方向，正反两条是同一条。

    不规范化就会在两个批次里各提一遍，唯一约束也拦不住——它比的是列值。
    """
    if a == b:
        raise ValueError("冲突的两端不能是同一条条款")
    return (a, b) if a < b else (b, a)


class PolicyConflict(Base):
    """两条条款在同一主题上互相打架。

    条款级而不是控制点级：`ControlRelation` 的唯一约束是
    (from_control_id, to_control_id, relation_type)，一对控制点只放得下一条
    conflicts_with，而同两份制度完全可能在两处打架（spec §1 决定①）。
    """

    __tablename__ = "policy_conflicts"
    __table_args__ = (
        UniqueConstraint("clause_a_id", "clause_b_id", "topic", name="uq_policy_conflict"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 存入前一律走 normalise_pair，保证 clause_a_id < clause_b_id。
    clause_a_id: Mapped[int] = mapped_column(
        ForeignKey("clauses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    clause_b_id: Mapped[int] = mapped_column(
        ForeignKey("clauses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    difference: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
