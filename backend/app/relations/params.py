"""关系推断的按语料标定参数。

这些值**依赖具体语料**：`MIN_SIMILARITY` 是拿一份 136 个控制点的语料、
9,180 对相似度分布标出来的，换一份文档集就不成立。写死在模块常量里意味着
换一个客户就要改代码重新部署，而且没人知道该改成多少。

所以：常量退化为**回落默认值**，实际取值来自 AppSetting，由 calibration.py
按语料自动标定后写入。读取与校验的形状照抄 review/thresholds.py。
"""

import math
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.models import AppSetting
from app.relations import clustering

PREFIX = "relation_"


@dataclass(frozen=True)
class RelationParams:
    top_k: int
    min_similarity: float
    max_cluster_size: int
    cluster_overlap: int
    pairs_per_batch: int
    max_batch_chars: int


#  键名 → (回落默认值, 下界, 上界, 是否整数)
_SPEC: dict[str, tuple[float, float, float, bool]] = {
    "top_k": (clustering.TOP_K_NEIGHBOURS, 1, 50, True),
    "min_similarity": (clustering.MIN_SIMILARITY, 0.0, 1.0, False),
    "max_cluster_size": (clustering.MAX_CLUSTER_SIZE, 2, 100, True),
    "cluster_overlap": (clustering.CLUSTER_OVERLAP, 0, 50, True),
    "pairs_per_batch": (clustering.PAIRS_PER_BATCH, 1, 100, True),
    "max_batch_chars": (clustering.MAX_BATCH_CHARS, 500, 100_000, True),
}


async def _value(session: AsyncSession, name: str) -> float:
    fallback, low, high, integral = _SPEC[name]
    setting = await session.get(AppSetting, PREFIX + name)
    if setting is None:
        return fallback
    try:
        raw = setting.value["value"]
        # bool 是 int 的子类，不挡掉的话 True 会被当成 1。
        if isinstance(raw, bool):
            return fallback
        value = float(raw)
    except (KeyError, TypeError, ValueError):
        return fallback
    if not math.isfinite(value) or not low <= value <= high:
        return fallback
    return round(value) if integral else value


async def load(session: AsyncSession) -> RelationParams:
    values = {name: await _value(session, name) for name in _SPEC}
    params = RelationParams(
        top_k=int(values["top_k"]),
        min_similarity=values["min_similarity"],
        max_cluster_size=int(values["max_cluster_size"]),
        cluster_overlap=int(values["cluster_overlap"]),
        pairs_per_batch=int(values["pairs_per_batch"]),
        max_batch_chars=int(values["max_batch_chars"]),
    )
    # overlap >= max_size 会让切分无法前进（split_cluster 会抛）。两个值各自
    # 合法、组合起来不合法，是配置项之间才有的失效方式，在这里挡掉。
    if params.cluster_overlap >= params.max_cluster_size:
        return RelationParams(**{**params.__dict__,
                                 "cluster_overlap": clustering.CLUSTER_OVERLAP,
                                 "max_cluster_size": clustering.MAX_CLUSTER_SIZE})
    return params


async def save(session: AsyncSession, name: str, value: float) -> None:
    """写单个参数。调用方负责审计留痕与提交。"""
    if name not in _SPEC:
        raise KeyError(name)
    key = PREFIX + name
    setting = await session.get(AppSetting, key)
    if setting is None:
        session.add(AppSetting(key=key, value={"value": value}))
    else:
        setting.value = {"value": value}
    await session.flush()
