from evals import corpus, metrics


def test_prf_counts_true_positives():
    report = metrics.prf({("A", "x"), ("A", "y")}, {("A", "x"), ("B", "z")})
    assert report == {"tp": 1, "predicted": 2, "expected": 2,
                      "precision": 0.5, "recall": 0.5, "f1": 0.5}


def test_candidate_recall_counts_positives_in_top_k():
    report = metrics.candidate_recall({"A": ["x", "q", "y"]}, {"A": {"x", "y"}}, k=2)
    assert report["hit"] == 1 and report["recall"] == 0.5


def test_base_control_folds_enhancements():
    assert metrics.base_control("AC-2.1") == "AC-2" == metrics.base_control("AC-2(1)")


def test_mapping_corpus_matches_the_k16_measurement_setup():
    """K16 的口径：106 个子类、191 个基础控制项（陈述 ≤ 250 字符）、716 个正例。"""
    pairs = corpus.olir_pairs()
    controls = corpus.mapping_controls()
    assert len(pairs) == 106
    assert sum(len(v) for v in pairs.values()) == 716
    assert len(controls) == 191
    assert all(len(c["statement"]) <= corpus.CONTROL_STATEMENT_CHARS for c in controls)


def test_extraction_corpus_is_deterministic():
    first, second = corpus.extraction_sections(), corpus.extraction_sections()
    assert first == second
    assert [family for family, _, _ in first] == list(corpus.EXTRACTION_FAMILIES)
    assert all(len(controls) == corpus.EXTRACTION_PER_FAMILY for _, _, controls in first)


def test_pair_details_split_misses_by_whether_the_model_saw_the_control():
    # K16 的下一步是人工看「OLIR 有、模型没给」：模型见过却没选，和预筛根本没给它看，
    # 是两件不同的事，清单里要分开。
    gold = {("PR.AA-01", "AC-2"), ("PR.AA-01", "IA-2"), ("PR.AA-02", "AC-3")}
    predicted = {
        ("PR.AA-01", "AC-2"): {"strength": "full", "rationale": "Accounts are managed."},
        ("PR.AA-02", "SC-7"): {"strength": "partial", "rationale": "Boundary protection."},
        ("GV.OC-01", "PM-1"): {"strength": "full", "rationale": "Not scored by OLIR."},
    }
    candidates = {"PR.AA-01": {"AC-2"}, "PR.AA-02": {"AC-3", "SC-7"}}

    details = metrics.pair_details(predicted, gold, candidates)

    assert details["missed"] == [
        {"csf": "PR.AA-01", "control": "IA-2", "in_candidates": False},
        {"csf": "PR.AA-02", "control": "AC-3", "in_candidates": True},
    ]
    # 只算 OLIR 覆盖到的子类，与 prf 的计分口径一致
    assert details["extra"] == [
        {"csf": "PR.AA-02", "control": "SC-7", "strength": "partial", "rationale": "Boundary protection."},
    ]


def test_pair_details_keep_the_strength_the_model_gave_each_hit():
    # OLIR 不分强弱：命中里模型标成「支撑」的不关缺口，要能单独拿出来人工看口径。
    gold = {("PR.AA-01", "AC-2"), ("PR.AA-01", "IA-2")}
    predicted = {
        ("PR.AA-01", "AC-2"): {"strength": "supporting", "rationale": "Accounts are managed."},
        ("PR.AA-01", "SC-7"): {"strength": "partial", "rationale": "Boundary protection."},
    }

    details = metrics.pair_details(predicted, gold, {"PR.AA-01": {"AC-2", "IA-2", "SC-7"}})

    assert details["hit"] == [
        {"csf": "PR.AA-01", "control": "AC-2", "strength": "supporting", "rationale": "Accounts are managed."},
    ]


def test_review_sample_is_reproducible_and_capped_by_the_population():
    from evals.review_sheet import sample

    pairs = {
        "missed": [{"csf": f"PR.AA-{n:02d}", "control": "AC-2", "in_candidates": n % 2 == 0}
                   for n in range(1, 41)],
        "extra": [{"csf": "PR.AA-01", "control": f"SC-{n}", "strength": "full", "rationale": "r"}
                  for n in range(1, 4)],
    }

    first = sample(pairs, n_seen=5, n_unseen=5, n_extra=25)

    assert first == sample(pairs, n_seen=5, n_unseen=5, n_extra=25)
    assert len(first["seen"]) == 5 and all(p["in_candidates"] for p in first["seen"])
    assert len(first["unseen"]) == 5 and not any(p["in_candidates"] for p in first["unseen"])
    assert len(first["extra"]) == 3
