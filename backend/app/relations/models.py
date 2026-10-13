from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.clauses.models import EMBEDDING_DIM
from app.db import Base


class ControlEmbedding(Base):
    """控制点的检索向量。

    单开一张表而不是给 controls 加列：`controls` 只能由 review.materialize()
    写入（铁律 2，源码扫描测试强制），而 embedding 是派生产物，索引任务要写它。
    控制点很短（title + statement），不分块，一条控制点一行。
    """

    __tablename__ = "control_embeddings"
    __table_args__ = (
        UniqueConstraint("control_id", name="uq_embedding_per_control"),
        Index("ix_control_embedding_model", "embedding_model"),
        Index(
            "ix_control_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    control_id: Mapped[int] = mapped_column(
        ForeignKey("controls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 先建行再异步向量化，所以可为空——与 ClauseChunk 同一形态。
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 渲染方式的版本。没有它，改 render_control 之后 embed_pending 看模型名没变，
    # 会把一库出自旧渲染的向量当成最新的，一条都不重算。
    embedding_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
