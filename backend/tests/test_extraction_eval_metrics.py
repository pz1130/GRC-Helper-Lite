from tests.test_extraction_quality import matches_groups


def test_common_word_alone_cannot_satisfy_a_gold_control():
    groups = [["privileged"], ["review"], ["quarter", "periodic"]]
    assert not matches_groups("Privileged accounts are created after approval", groups)
    assert matches_groups("Privileged access is reviewed quarterly", groups)


def test_all_concepts_are_required_and_whitespace_is_normalised():
    groups = [["standard change"], ["pre-approved"], ["template"]]
    assert matches_groups("STANDARD\n CHANGE uses a pre-approved template", groups)
    assert not matches_groups("Standard change uses an approved process", groups)
