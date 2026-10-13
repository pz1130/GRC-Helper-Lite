from types import SimpleNamespace

from app.mapping.batching import Batch, split_to_fit
from app.mapping.candidates import KeywordIndex, fuse, rank_candidates, tokenize


def test_tokenize_stems_and_drops_stopwords():
    assert tokenize("The Accounts are Managed") == ["account", "manag"]


def test_keyword_index_ranks_matching_controls_first():
    index = KeywordIndex([(1, "Backup and recovery"), (2, "Account management review"),
                          (3, "Privileged account access")])
    ranked = index.rank("privileged accounts")
    assert ranked[0] == 3 and 2 in ranked and 1 not in ranked


def test_fuse_is_deterministic_and_rewards_agreement():
    assert fuse([[1, 2, 3], [3, 2, 1]], top_k=2) == [1, 3]
    assert fuse([[1, 2], [2, 9]], top_k=1) == [2]


def test_vectors_join_keywords_when_available():
    items = [(10, "incident response")]
    controls = [(1, "Incident response plan"), (2, "Cryptographic keys"), (3, "Media sanitisation")]
    only_kw = rank_candidates(items, controls, top_k=3)[10]
    assert only_kw == [1]
    both = rank_candidates(
        items, controls, top_k=3,
        item_vectors={10: [1.0, 0.0]},
        control_vectors={1: [0.9, 0.1], 2: [0.0, 1.0], 3: [0.95, 0.0]},
    )[10]
    assert both[0] == 1 and 3 in both


def test_split_to_fit_halves_until_the_candidates_fit():
    items = [SimpleNamespace(id=i, code=f"X.{i}") for i in range(1, 5)]
    batch = Batch(items, "X", frozenset(range(1, 5)))
    candidates = {i: [i * 10, i * 10 + 1] for i in range(1, 5)}
    pieces = split_to_fit(batch, candidates, lambda ids: 100 * len(ids), max_chars=400)
    assert [sorted(p.mappable_ids) for p, _ in pieces] == [[1, 2], [3, 4]]
    assert pieces[0][1] == [10, 11, 20, 21]
