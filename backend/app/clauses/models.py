from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Index, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, TimestampMixin


class Clause(Base, TimestampMixin):
    """每一份解析出来的文档的**引用原子**——审计引用最终都落到这里。"""

    __tablename__ = "clauses"
    __table_args__ = (Index("ix_clauses_document_order", "document_id", "order_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("clauses.id", ondelete="CASCADE"), nullable=True
    )

    number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    heading: Mapped[str] = mapped_column(String(1000), nullable=False)
    heading_path: Mapped[str] = mapped_column(Text, nullable=False)
    citation_label: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    page_ref: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="section", nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    # 合并后的去向。不删行——控制点出处和审计都按 id 引用条款。
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("clauses.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    document: Mapped["Document"] = relationship(back_populates="clauses")  # noqa: F821


def active_clauses():
    return select(Clause).where(Clause.status != "merged")


class OriginalBlock(Base):
    """读取时的原文快照。拆分/合并只改关联，不修改这些文字和位置。"""

    __tablename__ = "original_blocks"
    __table_args__ = (
        UniqueConstraint("document_id", "snapshot", "source_key", name="uq_original_block_key"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key: Mapped[str] = mapped_column(String(200), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ClauseBlockSource(Base):
    """块级出处；拆开一个块时，两条新条款都可关联完整原文块。"""

    __tablename__ = "clause_block_sources"
    clause_id: Mapped[int] = mapped_column(
        ForeignKey("clauses.id", ondelete="CASCADE"), primary_key=True,
    )
    block_id: Mapped[int] = mapped_column(
        ForeignKey("original_blocks.id", ondelete="CASCADE"), primary_key=True, index=True,
    )


# pgvector 的列必须声明维度。固定 1536：OpenAI text-embedding-3-* 可用。
# 换用其他维度的模型 = 一次新迁移 + 一次全量重建。
EMBEDDING_DIM = 1536


class ClauseChunk(Base, TimestampMixin):
    """检索单元。

    引用锚点仍然是 Clause——chunk 只是为了让长条款能被切开检索。
    命中之后一律回指 clause，对外展示 clause.citation_label。
    """

    __tablename__ = "clause_chunks"
    __table_args__ = (
        UniqueConstraint("clause_id", "chunk_index", name="uq_chunk_per_clause"),
        Index("ix_chunk_document", "document_id"),
        Index("ix_chunk_embedding_model", "embedding_model"),
        Index("ix_chunk_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunk_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clause_id: Mapped[int] = mapped_column(
        ForeignKey("clauses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 刻意冗余：按文档过滤检索是高频操作，省掉每次 JOIN。随 clause 级联删除，不会漂移。
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # 先落库再异步向量化，所以可为空。
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)

    # 生成列：全文索引不需要应用层维护。语料为纯英文，用 english 配置。
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True), nullable=True
    )
