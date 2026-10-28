from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin


class SourceRelation(StrEnum):
    """条款是怎么支撑这个控制点的。"""

    DEFINES = "defines"  # 定义
    ELABORATES = "elaborates"  # 细化
    IMPLEMENTS = "implements"  # 实施


class RelationType(StrEnum):
    IMPLEMENTS = "implements"
    REFINES = "refines"
    DEPENDS_ON = "depends_on"
    CONFLICTS_WITH = "conflicts_with"  # M10 的冲突检测用
    DUPLICATES = "duplicates"


def canonicalize_relation_endpoints(
    from_id: int, to_id: int, relation_type: RelationType
) -> tuple[int, int]:
    """把关系端点规范化为可落库的顺序。

    `DUPLICATES` 是对称关系：A→B 与 B→A 含义相同。不规范化就会存下两条互为
    镜像、唯一约束 `uq_control_relation` 无法识别的记录。故对 `DUPLICATES`
    统一存成 `from_id < to_id`；其它（有方向的）关系原样返回，不重排端点。

    写入（`_materialize_relation`）、合并计划（`_plan_relations`）、合并执行
    （`merge_controls`）三处共用这一份实现——规则一旦分叉，后果静默。
    """
    if relation_type is RelationType.DUPLICATES and from_id > to_id:
        return to_id, from_id
    return from_id, to_id


def _enum(enum_cls, name: str):
    """不加 values_callable 会存枚举名而不是取值，而且测试看不出来（M1 踩过）。"""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        create_constraint=True,
    )


class Control(Base, TimestampMixin):
    """规范化控制点。

    **只能由 review.decide() 写入**（spec §4.4 铁律 2）。抽取任务和 Excel 导入
    都只产 Proposal，人工确认后才落到这张表。
    """

    __tablename__ = "controls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    # 合并后的去向。不删行——AnswerDraft.cited_control_ids 与审计日志都按 id 引用
    # 控制点，删掉这一行它们就指向空气了；留着才答得出「C-0058 去哪了」。
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("controls.id", ondelete="RESTRICT"), nullable=True, index=True
    )


def active_controls():
    """枚举控制点的统一起点。按 id 取的路径不要用它——历史引用要解析得出去向。"""
    return select(Control).where(Control.status != "merged")


class ControlSource(Base):
    """Control ↔ Clause，多对多。

    同一个控制点可以由多份文件的多个条款共同支撑——这是"不同文件里体现的
    控制点"这个需求的实现基础（spec §5.3）。
    """

    __tablename__ = "control_sources"
    __table_args__ = (UniqueConstraint("control_id", "clause_id", name="uq_control_clause"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    control_id: Mapped[int] = mapped_column(
        ForeignKey("controls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    clause_id: Mapped[int] = mapped_column(
        ForeignKey("clauses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation: Mapped[SourceRelation] = mapped_column(
        _enum(SourceRelation, "source_relation"), nullable=False
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_by_llm_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_call.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ControlRelation(Base):
    __tablename__ = "control_relations"
    __table_args__ = (
        UniqueConstraint(
            "from_control_id", "to_control_id", "relation_type", name="uq_control_relation"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_control_id: Mapped[int] = mapped_column(
        ForeignKey("controls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_control_id: Mapped[int] = mapped_column(
        ForeignKey("controls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation_type: Mapped[RelationType] = mapped_column(
        _enum(RelationType, "control_relation_type"), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
