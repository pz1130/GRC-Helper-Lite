import pytest

import app.models  # noqa: F401 — resolve Clause's Document relationship without database access
from app.clauses.models import Clause
from app.extraction.batching import MAX_BATCH_CHARS, MAX_BATCH_TARGETS, build_batches, render


def clause(cid, level=1, text="Body", heading="Heading"):
    return Clause(id=cid, document_id=1, number=str(cid), citation_label=str(cid),
                  level=level, text=text, heading=heading, order_index=cid)


def test_section_children_stay_together_up_to_the_target_limit():
    batches = build_batches([clause(1), clause(2, 2), clause(3, 3), clause(4)], max_targets=3)
    assert [[c.id for c in b.clauses] for b in batches] == [[1, 2, 3], [4]]


def test_orphans_and_empty_input():
    assert build_batches([]) == []
    batches = build_batches([clause(1, 3), clause(2, 2), clause(3)])
    assert [[c.id for c in b.clauses] for b in batches] == [[1, 2], [3]]


def test_a_section_is_split_by_target_count_not_only_by_size():
    """按章节塞满 12000 字时，一批可以有七八条条款：实测推理模型在这种批次上
    输出三四万 token 后丢掉 JSON 格式，14 条关键要求一条没抽到（G4，2026-09-27）。
    每批条款数由文档结构决定，不该由它决定模型成败。"""
    children = [clause(i, 2, "Teams must do this.") for i in range(2, 10)]
    for child in children:
        child.parent_id = 1
    batches = build_batches([clause(1), *children])
    assert [c.id for b in batches for c in b.clauses] == list(range(1, 10))
    assert all(sum(1 for c in b.clauses if c.text.strip()) <= MAX_BATCH_TARGETS for b in batches)
    assert MAX_BATCH_TARGETS == 2
    # 拆开之后，后面的批次仍然带着章节标题作为上下文
    assert all(1 in [c.id for c in b.context] for b in batches[1:])


def test_heading_only_clauses_do_not_use_up_the_target_limit():
    """没有正文的标题条款只要一条很短的审查结论，不占名额。"""
    clauses = [clause(1, text=""), clause(2, 2, text=""), clause(3, 3), clause(4, 3)]
    batches = build_batches(clauses)
    assert [[c.id for c in b.clauses] for b in batches] == [[1, 2, 3, 4]]


def test_split_accounts_for_rendering_overhead_preserves_order_and_section():
    clauses = [clause(i, 1 if i == 1 else 2, "x" * 80) for i in range(1, 8)]
    batches = build_batches(clauses, max_chars=300)
    assert len(batches) > 1
    assert [c.id for b in batches for c in b.clauses] == list(range(1, 8))
    assert all(len(render(b)) <= 300 for b in batches)
    assert {b.section for b in batches} == {"Heading"}


def test_oversized_clause_is_whole_and_alone():
    batches = build_batches([clause(1, text="x" * 500), clause(2, 2)], max_chars=100)
    assert [len(b.clauses) for b in batches] == [1, 1]
    assert batches[0].clauses[0].text == "x" * 500


def test_render_has_ids_labels_body_and_default_size():
    assert MAX_BATCH_CHARS == 12000
    text = render(build_batches([clause(42)])[0])
    assert "[clause_id=42] 42 — Heading" in text
    assert "Body" in text


@pytest.mark.parametrize("size", [0, -1])
def test_invalid_size(size):
    with pytest.raises(ValueError):
        build_batches([], max_chars=size)


def test_split_carries_all_ancestors_and_counts_context_size():
    root = clause(1, text="Only for production accounts.")
    parent = clause(2, 2, "The following checks are required quarterly.")
    parent.parent_id = 1
    children = [clause(i, 3, "Review privileged access. " * 5) for i in range(3, 7)]
    for child in children:
        child.parent_id = 2
    batches = build_batches([root, parent, *children], max_chars=700)
    assert [c.id for batch in batches for c in batch.clauses] == list(range(1, 7))
    assert len(batches) > 1
    for batch in batches:
        if any(c.id >= 3 for c in batch.clauses):
            assert {1, 2} <= set(batch.source_ids)
            assert "required quarterly" in render(batch)
        assert len(render(batch)) <= 700 or len(batch.clauses) == 1


def test_context_does_not_pull_unrelated_or_foreign_document_clauses():
    root = clause(1)
    child = clause(2, 2)
    child.parent_id = 99
    foreign = clause(99)
    foreign.document_id = 2
    batches = build_batches([root, child, foreign], max_chars=120)
    child_batch = next(b for b in batches if child in b.clauses)
    assert child_batch.context == []


def test_ancestor_cycle_terminates_without_duplicate_context():
    root, child = clause(1), clause(2, 2)
    root.parent_id, child.parent_id = 2, 1
    batches = build_batches([root, child], max_chars=100)
    assert all(len(b.source_ids) == len(set(b.source_ids)) for b in batches)


def test_parent_relationship_is_visible_even_within_a_single_batch():
    root, child = clause(1), clause(2, 2)
    child.parent_id = 1
    before = render(build_batches([root, child])[0])
    assert "[parent_clause_id=1]" in before
    child.parent_id = None
    assert render(build_batches([root, child])[0]) != before


def test_explicit_reference_brings_dependency_and_its_ancestor():
    scope = clause(1, text="Only production systems.")
    definition = clause(2, 2, text="The review interval is quarterly.")
    definition.parent_id = 1
    target = clause(3, text="Review access under section 2 of this policy.")
    batch = build_batches([scope, definition, target])[-1]
    assert batch.source_ids == [1, 2, 3]
    assert batch.references[0].status == "resolved"
    assert "quarterly" in render(batch)
    assert "from_clause_id=3" in render(batch)


def test_reference_cycle_terminates_and_targets_appear_once():
    a = clause(1, text="Use section 2 of this document.")
    b = clause(2, text="Use section 1 of this document.")
    batches = build_batches([a, b])
    assert [c.id for batch in batches for c in batch.clauses] == [1, 2]
    assert all(len(batch.source_ids) == 2 for batch in batches)
    assert all(len(batch.references) == 2 for batch in batches)


def test_unresolved_and_ambiguous_references_are_visible_not_guessed():
    a = clause(1, text="Follow section 9 of this policy and section 2 of this policy.")
    b, c = clause(2), clause(3)
    c.citation_label = "2"
    batch = build_batches([a, b, c])[0]
    assert batch.context == []
    assert [ref.status for ref in batch.references] == ["missing", "ambiguous"]
    assert "9: missing" in render(batch)


def test_external_or_unqualified_references_do_not_resolve_as_local():
    a = clause(1, text="See section 2 of another policy. Section 2 applies. ISO 27001.")
    batch = build_batches([a, clause(2)])[0]
    assert batch.context == [] and batch.references == []


def test_chinese_internal_reference_and_foreign_document_isolation():
    a = clause(1, text="按本制度第2条复核。按本文件第九条执行。")
    b = clause(2)
    foreign = clause(9)
    foreign.citation_label = "第九条"
    foreign.document_id = 2
    batch = build_batches([a, b, foreign])[0]
    assert batch.source_ids == [2, 1]
    assert [ref.status for ref in batch.references] == ["resolved", "missing"]


def test_large_reference_is_reported_without_truncating_it():
    a = clause(1, text="See section 2 of this document.")
    b = clause(2, text="x" * 7000)
    batches = build_batches([a, b])
    assert batches[0].context == []
    assert batches[0].references[0].status == "context_limit"
    assert batches[1].clauses[0].text == "x" * 7000


def test_reference_text_and_link_changes_invalidate_input():
    a = clause(1, text="See section 2 of this document.")
    b = clause(2, text="Review annually.")
    before = render(build_batches([a, b])[0])
    b.text = "Review quarterly."
    assert before != render(build_batches([a, b])[0])
    b.citation_label = "3"
    assert "2: missing" in render(build_batches([a, b])[0])
