from app.search.fulltext import RankedChunk
from app.search.fusion import RRF_K, fuse


def _ranked(ids: list[int]) -> list[RankedChunk]:
    return [RankedChunk(chunk_id=i, rank=p, score=1.0) for p, i in enumerate(ids, start=1)]


def test_empty_inputs_yield_nothing():
    assert fuse([], []) == []


def test_single_list_preserves_its_order():
    fused = fuse(_ranked([7, 8, 9]), [])
    assert [f.chunk_id for f in fused] == [7, 8, 9]


def test_a_chunk_ranked_by_both_beats_one_ranked_by_only_one():
    fused = fuse(_ranked([1, 2]), _ranked([2, 3]))
    assert fused[0].chunk_id == 2


def test_score_follows_the_rrf_formula():
    fused = fuse(_ranked([5]), _ranked([5]))
    assert fused[0].score == 2 / (RRF_K + 1)


def test_ranks_from_both_lists_are_reported():
    fused = fuse(_ranked([1, 2]), _ranked([2, 1]))
    by_id = {f.chunk_id: f for f in fused}
    assert by_id[1].rank_fulltext == 1
    assert by_id[1].rank_vector == 2


def test_missing_from_one_list_reports_none():
    fused = fuse(_ranked([1]), _ranked([2]))
    by_id = {f.chunk_id: f for f in fused}
    assert by_id[1].rank_vector is None
    assert by_id[2].rank_fulltext is None


def test_limit_is_respected():
    assert len(fuse(_ranked([1, 2, 3, 4, 5]), [], limit=2)) == 2


def test_results_are_sorted_by_score_descending():
    fused = fuse(_ranked([1, 2, 3]), _ranked([3, 2, 1]))
    scores = [f.score for f in fused]
    assert scores == sorted(scores, reverse=True)


def test_ties_break_deterministically_by_chunk_id():
    first = fuse(_ranked([2, 1]), _ranked([1, 2]))
    second = fuse(_ranked([2, 1]), _ranked([1, 2]))
    assert [f.chunk_id for f in first] == [f.chunk_id for f in second]


def test_k_is_the_documented_default():
    assert RRF_K == 60
