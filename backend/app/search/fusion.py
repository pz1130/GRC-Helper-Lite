"""倒数排名融合（Reciprocal Rank Fusion）。"""

from dataclasses import dataclass

from app.search.fulltext import RankedChunk

RRF_K = 60


@dataclass(frozen=True)
class FusedChunk:
    chunk_id: int
    score: float
    rank_fulltext: int | None
    rank_vector: int | None


def fuse(
    fulltext: list[RankedChunk],
    vector: list[RankedChunk],
    *,
    k: int = RRF_K,
    limit: int = 20,
) -> list[FusedChunk]:
    ranks_fulltext = {hit.chunk_id: hit.rank for hit in fulltext}
    ranks_vector = {hit.chunk_id: hit.rank for hit in vector}
    fused = [
        FusedChunk(
            chunk_id=chunk_id,
            score=sum(
                1.0 / (k + rank)
                for rank in (ranks_fulltext.get(chunk_id), ranks_vector.get(chunk_id))
                if rank is not None
            ),
            rank_fulltext=ranks_fulltext.get(chunk_id),
            rank_vector=ranks_vector.get(chunk_id),
        )
        for chunk_id in {*ranks_fulltext, *ranks_vector}
    ]
    fused.sort(key=lambda item: (-item.score, item.chunk_id))
    return fused[:limit]
