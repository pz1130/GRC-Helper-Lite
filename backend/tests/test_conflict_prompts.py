from app.conflicts.clustering import ClauseRef
from app.conflicts.prompts import (
    CONFLICT_SCHEMA,
    MAX_CONFLICTS,
    clause_chars,
    render_pairs,
)
from app.relations.clustering import Pair


def _bundles():
    return {
        1: [ClauseRef(91, "Password Policy", "4.2", "Passwords rotate every 90 days.")],
        2: [ClauseRef(92, "Access Standard", "7.1", "Passwords rotate every 180 days.")],
    }


def test_the_prompt_carries_clause_text_not_control_statements():
    rendered = render_pairs([Pair(1, 2, 0.9)], _bundles())

    # 决定②：数字在 statement 里已经丢了 78%，必须送原文。
    assert "90 days" in rendered
    assert "180 days" in rendered


def test_the_prompt_labels_each_clause_with_its_id_and_source():
    rendered = render_pairs([Pair(1, 2, 0.9)], _bundles())

    assert "[91]" in rendered and "[92]" in rendered
    assert "Password Policy" in rendered and "4.2" in rendered


def test_the_schema_caps_how_many_conflicts_one_answer_may_carry():
    # 与 M5 同一理由：输出越长模型越容易丢字段、把 JSON 写断。
    assert CONFLICT_SCHEMA["properties"]["conflicts"]["maxItems"] == MAX_CONFLICTS


def test_the_schema_requires_a_verbatim_quote_from_each_side():
    item = CONFLICT_SCHEMA["properties"]["conflicts"]["items"]
    assert "quote_a" in item["required"] and "quote_b" in item["required"]
    assert item["additionalProperties"] is False


def test_clause_chars_sums_every_clause_of_a_control():
    bundles = {1: [ClauseRef(91, "D", "4.2", "abcde"), ClauseRef(93, "D", "4.3", "fg")]}

    assert clause_chars(bundles)[1] == 7
