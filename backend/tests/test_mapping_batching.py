from types import SimpleNamespace

import pytest

from app.errors import AppError
from app.mapping.batching import (
    MAX_CONTROL_CONTEXT_CHARS,
    build_batches,
    render_controls,
    render_items,
)


def item(id, code, level, title="t", description="d", parent=None, attributes=None):
    """替身要带齐 FrameworkItem 的字段：分批要靠 parent_id 与 attributes 判定要求项。"""
    return SimpleNamespace(
        id=id, code=code, level=level, title=title, description=description,
        parent_id=parent, attributes=attributes,
    )


def test_each_top_level_subtree_becomes_its_own_batch():
    items = [
        item(1, "PR", 1), item(2, "PR.AA", 2), item(3, "PR.AA-01", 3),
        item(4, "DE", 1), item(5, "DE.CM", 2),
    ]
    batches = build_batches(items)
    assert [[i.code for i in batch.items] for batch in batches] == [
        ["PR", "PR.AA", "PR.AA-01"], ["DE", "DE.CM"]]
    assert batches[0].section == "PR"


def test_an_oversized_subtree_splits_without_splitting_an_item():
    big = "x" * 5000
    items = [item(1, "AC", 1)] + [
        item(number, f"AC-{number}", 2, description=big) for number in range(2, 8)]
    batches = build_batches(items, max_chars=12000)
    assert len(batches) > 1
    assert sum(len(batch.items) for batch in batches) == len(items)
    assert all(batch.items for batch in batches)


def test_a_single_oversized_item_stays_intact_in_its_own_batch():
    items = [item(1, "AC", 1, description="x" * 40000)]
    batches = build_batches(items, max_chars=12000)
    assert len(batches) == 1 and len(batches[0].items) == 1


def test_render_items_exposes_id_code_and_description():
    text = render_items(build_batches([item(1, "PR.AA-01", 1, "Ident", "Body text.")])[0])
    assert "[framework_item_id=1]" in text
    assert "PR.AA-01" in text and "Ident" in text and "Body text." in text


def test_render_controls_exposes_id_and_statement():
    controls = [SimpleNamespace(id=9, code="C-0009", title="Dual approval",
                                statement="Two approvers required.")]
    text = render_controls(controls)
    assert "[control_id=9]" in text
    assert "C-0009" in text and "Two approvers required." in text


def test_control_context_over_the_hard_limit_fails_loudly():
    controls = [
        SimpleNamespace(id=number, code=f"C-{number:04d}", title="t", statement="x" * 1000)
        for number in range(1, 200)
    ]
    with pytest.raises(AppError) as excinfo:
        render_controls(controls)
    assert str(MAX_CONTROL_CONTEXT_CHARS) in str(excinfo.value)
    assert "199" in str(excinfo.value)


def test_empty_input_produces_no_batches():
    assert build_batches([]) == []


def test_only_requirement_items_are_offered_as_mapping_targets():
    """容器（有子节点的条目）不是要求项，映过去不消除任何差距。

    覆盖度分母用的就是这个定义；映射目标必须用同一套，否则两边各说各话。
    """
    items = [
        item(1, "RS", 1),                       # Function，容器
        item(2, "RS.AN", 2, parent=1),          # Category，容器（CSF 里有正文）
        item(3, "RS.AN-06", 3, parent=2),       # Subcategory，要求项
        item(4, "RS.AN-07", 3, parent=2),
    ]
    batch = build_batches(items)[0]
    assert batch.mappable_ids == frozenset({3, 4})
    assert [i.code for i in batch.items] == ["RS", "RS.AN", "RS.AN-06", "RS.AN-07"]


def test_an_explicit_requirement_flag_overrides_the_leaf_rule():
    """800-53 的 AC-2 本身是真控制，AC-2.1 是它的子节点。"""
    items = [
        item(1, "AC", 1),
        item(2, "AC-2", 2, parent=1),
        item(3, "AC-2.1", 3, parent=2),
    ]
    items[1].attributes = {"is_requirement": True}
    items[0].attributes = items[2].attributes = None
    assert build_batches(items)[0].mappable_ids == frozenset({2, 3})


def test_containers_render_without_an_id_so_they_cannot_be_cited():
    items = [item(1, "CA", 1, description=""), item(2, "CA-1", 2, parent=1, description="Body.")]
    text = render_items(build_batches(items)[0])
    assert "[framework_item_id=2]" in text
    assert "[framework_item_id=1]" not in text
    assert "CA" in text                       # 容器仍作为上下文出现


def test_a_batch_with_no_requirement_items_is_dropped():
    """整批都不是要求项时不该浪费一次模型调用。

    真实场景：字符预算把某个 Family 的控制项全推到下一块，首块只剩 Family 表头。
    这里用显式标记构造——无子节点的 Family 按「叶子即要求」本身就是要求项。
    """
    containers = [
        item(1, "CA", 1, attributes={"is_requirement": False}),
        item(2, "CM", 1, attributes={"is_requirement": False}),
    ]
    assert build_batches(containers) == []


def test_control_render_does_not_put_the_code_where_the_id_belongs():
    """模型曾把 C-0006 当成 control_id 交上来。"""
    controls = [SimpleNamespace(id=9, code="C-0009", title="Dual approval",
                                statement="Two approvers required.")]
    text = render_controls(controls)
    assert text.startswith("[control_id=9]")
    assert "Dual approval" in text and "C-0009" in text
    # id 标记之后紧跟的不能是另一个像标识符的编号
    assert not text.split("]", 1)[1].lstrip().startswith("C-0009")
