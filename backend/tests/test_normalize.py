import pytest

from app.llm.validation import normalize


def test_normalize_folds_presentation_but_preserves_wording():
    assert normalize("  Two\n Approvers,\tplease. ") == "Two Approvers, please."
    assert normalize("Acme’s “closed” – x") == "Acme's \"closed\" - x"
    assert normalize("third-\nparty") == "third-party"
    assert normalize("• a\n• b") == "a b"


def test_extract_json_ignores_reasoning_blocks():
    """推理模型（minimax-m3）以 <think> 思维链开头。

    兜底逻辑「取第一个 { 到最后一个 }」会从推理文本里抠出模型思考时举的
    例子，于是校验报出误导性的字段缺失，而真正的问题是 JSON 压根没写出来。
    """
    from app.llm.validation import ValidationFailure, extract_json

    answer = '{"mappings": [], "insufficient_evidence": true}'
    assert extract_json(f"<think>maybe {{\"a\": 1}} works</think>{answer}") == {
        "mappings": [], "insufficient_evidence": True}
    assert extract_json(f"<think>reasoning</think>\n```json\n{answer}\n```") == {
        "mappings": [], "insufficient_evidence": True}

    # 预算耗尽在思考中途——没有答案就该明确报「没有 JSON」，
    # 而不是把推理里的片段当成答案。
    with pytest.raises(ValidationFailure):
        extract_json('<think>I could emit {"framework_item_id": 3} but wait')
