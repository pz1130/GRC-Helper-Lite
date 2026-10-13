"""抽取提示词里那几条约束，是以后唯一能证明它们曾经存在的地方。

两条都是「引文正确、但控制点与原文的关系被扭曲」那一类（OQ-10 / OQ-11），
闸 3 与闸 4 结构上查不出来：它们只保证没有编造文字。
"""

from app.extraction.prompts import EXTRACT_SCHEMA, EXTRACT_SYSTEM


def test_the_prompt_forbids_upgrading_the_source_s_normative_strength():
    assert "PRESERVE THE SOURCE'S NORMATIVE STRENGTH" in EXTRACT_SYSTEM
    assert "never upgrade it to 'must' or 'shall'" in EXTRACT_SYSTEM


def test_the_prompt_prefers_the_normative_clause_over_a_restating_definition():
    # 术语表复述规范要求时，它往往是全文规范措辞最密集的一条，对模型最有吸引力。
    # 约束是**有条件**的：只有两边都写了才偏向规范章节，术语表是唯一出处时照引不误，
    # 否则这一条会变成新的漏抽。
    assert "restated" in EXTRACT_SYSTEM and "definition" in EXTRACT_SYSTEM
    assert "only place" in EXTRACT_SYSTEM


def test_a_control_still_has_to_cite_something():
    citations = EXTRACT_SCHEMA["properties"]["controls"]["items"]["properties"]["citations"]
    assert citations["minItems"] == 1
