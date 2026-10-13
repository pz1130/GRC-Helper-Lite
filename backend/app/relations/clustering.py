"""双通道候选生成——全程无 AI，纯 SQL 与向量查询。

两条通道的批次形状不同，因为两种关系的拓扑不同：

- duplicates 是「一对」的属性，向量相似度天然产出对，按对批。
  不用连通分量分簇：相似度具传递性，容易把控制点连成一个巨型分量。
- depends_on 是流程属性（「先审批，后实施」），模型要看到整段顺序才判得准，
  按章节簇批。相似度对它无效——A 依赖 B 不意味着 A 像 B。

两条通道都受**双重封顶**：条数与字符预算。只按条数封顶时，20 条长
statement 的簇渲染出的 prompt 会远超同一仓库在别处强制的预算，重演 M5
「批内组合空间一大，模型输出就开始失控」那一幕。
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.clauses.models import Clause
from app.controls.models import ControlSource
from app.parsing.flatten import PATH_SEPARATOR
from app.relations.models import ControlEmbedding

TOP_K_NEIGHBOURS = 8
# 首轮实测后调整（设计 §4 就是这么写的：起步值，有数据再定）。
# statement-only 渲染下，设计点名的那组已知重复最低一对是 0.942，全量 9180 对
# 的 p90=0.884、p99=0.958。0.90 留出了余量又砍掉了绝大部分噪声；0.75 是在旧
# 渲染下拍的，那时它对应 1900+ 对候选、约 180 批。
MIN_SIMILARITY = 0.90
MAX_CLUSTER_SIZE = 20
CLUSTER_OVERLAP = 3
PAIRS_PER_BATCH = 25
# 沿用 M5 修正后的值；mapping/batching.py 用的是同一个数。
MAX_BATCH_CHARS = 6000


class Pair(NamedTuple):
    low: int
    high: int
    similarity: float


@dataclass(frozen=True)
class Cluster:
    control_ids: list[int]
    label: str


def _sizer(sizes: Mapping[int, int] | None) -> Callable[[int], int]:
    return (lambda control_id: sizes.get(control_id, 0)) if sizes else (lambda _: 0)


def split_cluster(
    ids: list[int],
    *,
    max_size: int,
    overlap: int,
    sizes: Mapping[int, int] | None = None,
    max_chars: int = MAX_BATCH_CHARS,
) -> list[list[int]]:
    """按顺序切分，相邻块保留重叠，避免切口处的依赖被切断。

    块的大小由条数与字符预算里先触顶的那个决定。单条就超预算时它独占一块——
    宁可超预算，也不把一个控制点从语料里丢掉。
    """
    if max_size <= overlap:
        raise ValueError("overlap 必须小于 max_size，否则切分无法前进")
    if max_chars <= 0:
        raise ValueError("max_chars 必须为正")
    cost = _sizer(sizes)

    chunks: list[list[int]] = []
    start = 0
    while start < len(ids):
        end = start
        used = 0
        while end < len(ids):
            length = cost(ids[end])
            # end > start 保证每块至少放得下一条，否则单条超预算会原地空转。
            if end > start and (end - start >= max_size or used + length > max_chars):
                break
            used += length
            end += 1
        chunks.append(ids[start:end])
        if end >= len(ids):
            break
        # 退回 overlap 条再起下一块；至少前进一条，否则死循环。
        start += max(1, (end - start) - overlap)
    return chunks


def batch_pairs(
    pairs: list[Pair],
    *,
    sizes: Mapping[int, int] | None = None,
    max_pairs: int = PAIRS_PER_BATCH,
    max_chars: int = MAX_BATCH_CHARS,
) -> list[list[Pair]]:
    """按对数与字符预算双重封顶；单对超预算时自成一批。"""
    if max_pairs <= 0:
        raise ValueError("max_pairs 必须为正")
    if max_chars <= 0:
        raise ValueError("max_chars 必须为正")
    cost = _sizer(sizes)

    batches: list[list[Pair]] = []
    current: list[Pair] = []
    used = 0
    for pair in pairs:
        length = cost(pair.low) + cost(pair.high)
        if current and (len(current) >= max_pairs or used + length > max_chars):
            batches.append(current)
            current, used = [], 0
        current.append(pair)
        used += length
    if current:
        batches.append(current)
    return batches


async def duplicate_pairs(
    session: AsyncSession,
    *,
    top_k: int = TOP_K_NEIGHBOURS,
    min_similarity: float = MIN_SIMILARITY,
) -> list[Pair]:
    """每个控制点取 top-k 近邻，过阈值后按 (low, high) 去重。

    只在**同一 embedding 模型**的向量之间比较：跨模型的余弦距离没有意义，
    而换模型时的回填是逐批落盘的，中途必然存在新旧并存的窗口。回填只做了
    一半时，两组各自成对，不会互相污染。

    行序与并列名次都定死：批次指纹是对渲染后的 prompt 算的，顺序一变，
    重试时的检查点就全部落空，整轮重跑并再提一遍同样的对。
    """
    rows = list(
        await session.scalars(
            select(ControlEmbedding)
            .where(ControlEmbedding.embedding.is_not(None))
            .order_by(ControlEmbedding.control_id)
        )
    )
    other = aliased(ControlEmbedding)
    best: dict[tuple[int, int], float] = {}
    for row in rows:
        distance = other.embedding.cosine_distance(row.embedding)
        neighbours = await session.execute(
            select(other.control_id, distance.label("distance"))
            .where(
                other.embedding.is_not(None),
                other.control_id != row.control_id,
                other.embedding_model.is_not_distinct_from(row.embedding_model),
            )
            .order_by(distance, other.control_id)
            .limit(top_k)
        )
        for control_id, raw in neighbours:
            similarity = 1.0 - float(raw)
            if similarity < min_similarity:
                continue
            key = (min(row.control_id, control_id), max(row.control_id, control_id))
            best[key] = max(best.get(key, 0.0), similarity)
    return [
        Pair(low, high, score)
        # 同分时按 (low, high) 定序，否则名次取决于堆扫描顺序。
        for (low, high), score in sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


async def section_clusters(
    session: AsyncSession,
    *,
    sizes: Mapping[int, int] | None = None,
    max_size: int = MAX_CLUSTER_SIZE,
    overlap: int = CLUSTER_OVERLAP,
    max_chars: int = MAX_BATCH_CHARS,
) -> list[Cluster]:
    """按 (document_id, heading_path 顶层) 分簇，保持 order_index 顺序。"""
    rows = await session.execute(
        select(ControlSource.control_id, Clause.document_id, Clause.heading_path)
        .join(Clause, Clause.id == ControlSource.clause_id)
        .order_by(Clause.document_id, Clause.order_index, ControlSource.control_id)
    )
    grouped: dict[tuple[int, str], list[int]] = {}
    for control_id, document_id, heading_path in rows:
        top = (heading_path or "").split(PATH_SEPARATOR.strip())[0].strip()
        bucket = grouped.setdefault((document_id, top), [])
        # 同一控制点可由同章节多条条款支撑，只保留一次且保持首次出现的顺序。
        if control_id not in bucket:
            bucket.append(control_id)

    clusters: list[Cluster] = []
    for (document_id, top), ids in grouped.items():
        # 一个控制点组不成对，没有关系可推。
        if len(ids) < 2:
            continue
        for position, chunk in enumerate(
            split_cluster(
                ids, max_size=max_size, overlap=overlap, sizes=sizes, max_chars=max_chars
            ),
            start=1,
        ):
            if len(chunk) < 2:
                continue
            label = f"doc{document_id}:{top}" + (f" #{position}" if position > 1 else "")
            clusters.append(Cluster(chunk, label))
    return clusters
