from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProposalKind(StrEnum):
    """spec §6.5 的八个 AI 任务，加上 M4 的 Excel 列映射（§6.7）。"""

    CONTROL_EXTRACT = "control_extract"
    MAPPING = "mapping"
    RELATION = "relation"
    CONFLICT = "conflict"
    ANSWER = "answer"
    MATURITY_SCORE = "maturity_score"
    EVIDENCE_SUGGESTION = "evidence_suggestion"
    AUDIT_PREDICTION = "audit_prediction"
    MATRIX_MAPPING = "matrix_mapping"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    MODIFIED = "modified"
    REJECTED = "rejected"
    # 这一批根本没产出东西（模型输出不合 schema、引用对不上原文……）。
    # 它**不是**待人决策的提案，所以不进待确认计数；但必须在队列里看得见，
    # 否则这一批就静默消失了，而审计员会以为文档就只抽出这些控制点。
    # 失败原因写在 reject_reason 里，llm_call_id 指回那次调用。
    FAILED = "failed"


def _enum(enum_cls, name: str):
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        create_constraint=True,
    )


class Proposal(Base):
    """所有 AI 产出的统一载体（spec §5.10、§4.4 铁律 2）。

    一张表装九种产出。做成九张表会让确认队列变成九套几乎一样的代码，
    而它们真正的差别只在 payload 的形状——那正是 JSONB 该干的事。

    payload 永远保留**模型原始输出**；人工改过的版本进 decided_payload。
    两者都留着，才能回答"AI 当初说了什么、人改了什么"。
    """

    __tablename__ = "proposals"
    __table_args__ = (
        Index("ix_proposal_queue", "status", "confidence"),
        Index("ix_proposal_document", "document_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[ProposalKind] = mapped_column(_enum(ProposalKind, "proposal_kind"), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    citations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    llm_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_call.id", ondelete="SET NULL"), nullable=True
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )

    status: Mapped[ProposalStatus] = mapped_column(
        _enum(ProposalStatus, "proposal_status"), default=ProposalStatus.PENDING, nullable=False
    )
    decided_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
