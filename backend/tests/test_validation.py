import pytest

from app.llm.validation import (
    NullCitationValidator,
    ValidationFailure,
    correction_prompt,
    extract_json,
    validate,
)

SCHEMA = {
    "type": "object",
    "required": ["controls"],
    "properties": {
        "controls": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "confidence"],
                "properties": {
                    "title": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "insufficient_evidence": {"type": "boolean"},
    },
}


def test_extract_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_from_fenced_block():
    raw = '这是我的分析：\n```json\n{"a": 1}\n```\n希望有帮助。'
    assert extract_json(raw) == {"a": 1}


def test_extract_json_from_surrounding_prose():
    raw = '好的，结果如下 {"a": 1, "b": [2, 3]} 以上。'
    assert extract_json(raw) == {"a": 1, "b": [2, 3]}


def test_extract_json_raises_when_absent():
    with pytest.raises(ValidationFailure) as exc:
        extract_json("我没法回答这个问题。")
    assert "JSON" in exc.value.reason


def test_validate_accepts_conforming_payload():
    payload = {"controls": [{"title": "特权账号双人复核", "confidence": 0.87}]}
    assert validate(payload, SCHEMA) == payload


def test_validate_rejects_missing_required_field():
    with pytest.raises(ValidationFailure) as exc:
        validate({"controls": [{"title": "缺置信度"}]}, SCHEMA)
    assert "confidence" in exc.value.reason


def test_validate_rejects_out_of_range_confidence():
    with pytest.raises(ValidationFailure):
        validate({"controls": [{"title": "x", "confidence": 1.5}]}, SCHEMA)


def test_insufficient_evidence_is_a_valid_answer():
    """spec §6.3 第 7 道闸：模型必须被允许说'证据不足'。"""
    payload = {"controls": [], "insufficient_evidence": True}
    assert validate(payload, SCHEMA) == payload


def test_correction_prompt_contains_the_reason():
    prompt = correction_prompt('{"bad": 1}', "'confidence' is a required property")
    assert "confidence" in prompt
    assert '{"bad": 1}' in prompt


@pytest.mark.asyncio
async def test_null_citation_validator_always_passes():
    assert await NullCitationValidator().check({"anything": True}) is None
