"""控制点向量化。

本模块不写网络代码：脱敏、留痕、预算检查全由 M1 的 llm.embed() 完成。
控制点很短，不分块——一条控制点一行，与 ClauseChunk 的分块逻辑无关。
"""

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.controls.models import Control, active_controls
from app.db import session_factory
from app.indexing.embedder import current_model
from app.llm.runner import embed
from app.relations.models import ControlEmbedding

BATCH_SIZE = 64
# 改这个字符串就等于改了向量的含义，必须同时进 stale 判定，否则换渲染方式
# 后 embed_pending 看模型名没变，会认为一切都是新的、一条都不重算。
# v1 = code + title + statement（M6 首版）；v2 = 只要 statement，见 render_control。
EMBEDDING_VERSION = "v2"
# 这个函数是在 HTTP 请求里同步跑的，不是后台任务。默认只做两批往返，剩下的
# 靠再点一次——响应里的 pending 就是给前端显示「还剩多少」用的。
DEFAULT_LIMIT = BATCH_SIZE * 2


def render_control(control: Any) -> str:
    """只喂 statement。

    实测（136 个控制点、9180 对、embo-01）：把 code 与 title 一起喂进去会淹掉
    信号。以设计 §1 决定②点名的那组已知重复（C-0001/C-0002/C-0005）为标尺，
    「排在最差的一对已知重复之前的噪声对」——

        code + title + statement   最差 0.786，噪声 1916 对
        title + statement          最差 0.705，噪声 4081 对
        statement only             最差 0.942，噪声  207 对

    title 是主要毒源而非 code：去掉 code 反而更差。控制点标题是抽取时生成的，
    同一件事在不同文档里被命名成三个样子，正是它把重复对推开的。
    """
    return control.statement or control.title or ""


async def embed_pending(
    session: AsyncSession, *, limit: int = DEFAULT_LIMIT
) -> dict[str, Any]:
    """给尚无向量、或向量出自旧模型的控制点补算。幂等，可重复调用。"""
    model = await current_model(session)
    stale = (
        active_controls()
        .outerjoin(ControlEmbedding, ControlEmbedding.control_id == Control.id)
        .where(
            or_(
                ControlEmbedding.id.is_(None),
                ControlEmbedding.embedding.is_(None),
                ControlEmbedding.embedding_model.is_distinct_from(model),
                ControlEmbedding.embedding_version.is_distinct_from(EMBEDDING_VERSION),
            )
        )
        .order_by(Control.id)
    )
    controls = list(await session.scalars(stale.limit(limit)))
    if not controls:
        return {"embedded": 0, "model": model, "pending": 0}

    existing = {
        row.control_id: row
        for row in await session.scalars(
            select(ControlEmbedding).where(
                ControlEmbedding.control_id.in_([c.id for c in controls])
            )
        )
    }

    embedded = 0
    for start in range(0, len(controls), BATCH_SIZE):
        batch = controls[start : start + BATCH_SIZE]
        vectors, _ = await embed(session, texts=[render_control(c) for c in batch])
        for control, vector in zip(batch, vectors, strict=True):
            # 换模型是重算，不是新增行——一条控制点始终只有一行向量。
            row = existing.get(control.id)
            if row is None:
                row = ControlEmbedding(control_id=control.id)
                session.add(row)
            row.embedding = vector
            row.embedding_model = model
            row.embedding_version = EMBEDDING_VERSION
            embedded += 1
        await session.flush()
        # 逐批落盘，与 indexing/embedder.py 同一条理由：上游中途失败时，已完成
        # 的批次不丢，embed() 在 finally 里写的 LLMCall 合规留痕也不跟着回滚。
        await session.commit()

    pending = await session.scalar(
        select(func.count()).select_from(stale.subquery())
    )
    return {"embedded": embedded, "model": model, "pending": int(pending or 0)}


async def embed_controls(ctx: dict[str, Any]) -> dict[str, Any]:
    """新控制点确认后的后台回填任务；幂等，重复排队不会重复建行。"""
    del ctx
    async with session_factory() as session:
        return await embed_pending(session)
