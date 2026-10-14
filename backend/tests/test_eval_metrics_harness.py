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
