from types import SimpleNamespace

import pytest

from app.llm.validation import ValidationFailure, validate
from app.relations.prompts import (
    DEPENDS_SYSTEM,
    DUPLICATE_SYSTEM,
    RELATION_SCHEMA,
    render_cluster,
    render_pairs,
)


def control(id, code, title="t", statement="s"):
    return SimpleNamespace(id=id, code=code, title=title, statement=statement)


def relation(**overrides):
    return {
        "from_control_id": 1, "to_control_id": 2,
        "from_quote": "a", "to_quote": "b",
        "rationale": "because they overlap", "confidence": 0.8,
        **overrides,
    }


def test_the_batch_size_and_the_answer_cap_are_separate_quantities():
    """两个都是 25，但一个是「一批并排几对」，一个是「一次回答封顶几条」。

    批次大小属于候选生成，和 TOP_K / MAX_CLUSTER_SIZE 放在一起；回答上限属于
    提示词，必须和 schema 的 maxItems 是同一个数，否则两处会各自漂移。
    """
    from app.relations.clustering import PAIRS_PER_BATCH
    from app.relations.prompts import MAX_RELATIONS

    assert PAIRS_PER_BATCH == 25
    assert MAX_RELATIONS == 25
    assert RELATION_SCHEMA["properties"]["relations"]["maxItems"] == MAX_RELATIONS
    for system in (DUPLICATE_SYSTEM, DEPENDS_SYSTEM):
        assert f"at most {MAX_RELATIONS} relations" in system


def test_neither_prompt_asks_the_model_to_choose_a_relation_type():
    assert "relation_type" not in RELATION_SCHEMA["properties"]["relations"]["items"]["properties"]
    for system in (DUPLICATE_SYSTEM, DEPENDS_SYSTEM):
        assert "integers inside the square brackets" in system


def test_parallel_tiers_of_one_requirement_are_not_duplicates():
    # P1/P2/P3/P4 那四条 SLA 措辞逐字相同、相似度 0.99，只有档位和时限不同。
    # 阈值动不了它们——它们本来就在最顶端，只能靠判定这一级说清楚（OQ-7）。
    assert "priority" in DUPLICATE_SYSTEM and "threshold" in DUPLICATE_SYSTEM
    assert "meeting one does not meet the other" in DUPLICATE_SYSTEM


def test_the_two_prompts_ask_different_questions():
    assert "same requirement" in DUPLICATE_SYSTEM
    assert "depends on" in DEPENDS_SYSTEM
    assert DUPLICATE_SYSTEM != DEPENDS_SYSTEM


def test_render_pairs_shows_both_sides_with_ids():
    text = render_pairs([(control(1, "C-0001"), control(2, "C-0002"), 0.91)])
    assert "[control_id=1]" in text and "[control_id=2]" in text
    assert "C-0001" in text and "C-0002" in text


def test_render_cluster_preserves_order():
    text = render_cluster([control(3, "C-0003"), control(1, "C-0001")])
    assert text.index("C-0003") < text.index("C-0001"), "簇内顺序即流程顺序，不得重排"


def test_schema_requires_both_quotes():
    for missing in ("from_quote", "to_quote"):
        with pytest.raises(ValidationFailure):
            validate({"relations": [
                {k: v for k, v in relation().items() if k != missing}]}, RELATION_SCHEMA)


def test_schema_caps_the_number_of_relations():
    assert RELATION_SCHEMA["properties"]["relations"]["maxItems"] == 25
    with pytest.raises(ValidationFailure):
        validate({"relations": [relation() for _ in range(26)]}, RELATION_SCHEMA)


def test_abstention_shape_is_legal():
    assert validate({"relations": [], "insufficient_evidence": True}, RELATION_SCHEMA)
    with pytest.raises(ValidationFailure):
        validate({"relations": []}, RELATION_SCHEMA)
