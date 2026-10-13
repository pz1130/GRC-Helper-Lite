from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.relations.citations import RelationCitationValidator

FROM_TEXT = "Every change must be approved by the CAB before implementation."
TO_TEXT = "The implementer shall follow the approved implementation plan."


def relation(**overrides):
    return {
        "from_control_id": 1,
        "to_control_id": 2,
        "from_quote": "approved by the CAB",
        "to_quote": "follow the approved implementation plan",
        "rationale": "implementation depends on prior approval",
        "confidence": 0.8,
        **overrides,
    }


def validator(control_ids=frozenset({1, 2})):
    session = SimpleNamespace(scalars=AsyncMock(return_value=[
        SimpleNamespace(id=1, statement=FROM_TEXT),
        SimpleNamespace(id=2, statement=TO_TEXT),
    ]))
    return RelationCitationValidator(session, control_ids=control_ids)


async def test_a_literal_quote_at_both_ends_passes():
    assert await validator().check({"relations": [relation()]}) is None


async def test_typographic_variants_are_folded():
    payload = {"relations": [relation(from_quote="approved by the CAB")]}
    assert await validator().check(payload) is None


@pytest.mark.parametrize("kwargs", [
    pytest.param({"from_control_id": 999}, id="gate3-from-not-in-batch"),
    pytest.param({"to_control_id": 999}, id="gate3-to-not-in-batch"),
    pytest.param({"from_control_id": 2, "to_control_id": 2}, id="self-relation"),
    pytest.param({"from_quote": "rejected by the CAB"}, id="gate4-from-fabricated"),
    pytest.param({"to_quote": "ignore the plan"}, id="gate4-to-fabricated"),
    pytest.param({"from_quote": "follow the approved implementation plan"},
                 id="gate4-quotes-swapped"),
    pytest.param({"from_quote": "   "}, id="blank-quote"),
    pytest.param({"from_control_id": True}, id="bool-is-not-an-id"),
    pytest.param({"confidence": 1.5}, id="confidence-out-of-range"),
])
async def test_invalid_relations_rejected(kwargs):
    assert await validator().check({"relations": [relation(**kwargs)]})


async def test_empty_list_requires_the_abstention_flag():
    assert await validator().check({"relations": []})
    assert await validator().check(
        {"relations": [], "insufficient_evidence": True}) is None


async def test_abstention_cannot_accompany_relations():
    assert await validator().check(
        {"relations": [relation()], "insufficient_evidence": True})


async def test_every_relation_is_checked_not_just_the_first():
    payload = {"relations": [relation(), relation(from_quote="fabricated")]}
    assert await validator().check(payload)


def _pair_validator(allowed_pairs=None, control_ids=frozenset({1, 2, 3, 4})):
    """四个控制点、两对候选：(1,2) 与 (3,4)。"""
    session = SimpleNamespace(scalars=AsyncMock(return_value=[
        SimpleNamespace(id=1, statement=FROM_TEXT),
        SimpleNamespace(id=2, statement=TO_TEXT),
        SimpleNamespace(id=3, statement=FROM_TEXT),
        SimpleNamespace(id=4, statement=TO_TEXT),
    ]))
    return RelationCitationValidator(
        session, control_ids=control_ids, allowed_pairs=allowed_pairs
    )


async def test_a_relation_across_two_different_candidate_pairs_is_rejected():
    """闸 3 只查「两端都在本批次」时，25 对并排等于 50 个控制点随便连。

    第 1 对的左边与第 2 对的右边从来不是相似度候选，也从未并排出现过；
    只按批次成员判是查不出来的。
    """
    check = _pair_validator(allowed_pairs=[(1, 2), (3, 4)])
    crossed = relation(from_control_id=1, to_control_id=4)
    reason = await check.check({"relations": [crossed]})
    assert reason is not None and "are not a pair given side by side in this batch" in reason


async def test_a_genuine_candidate_pair_still_passes():
    check = _pair_validator(allowed_pairs=[(1, 2), (3, 4)])
    assert await check.check({"relations": [relation()]}) is None


async def test_the_pair_check_is_symmetric():
    """候选对是无向的：给的是 (1, 2)，模型答 2→1 也算同一对。"""
    check = _pair_validator(allowed_pairs=[(1, 2)])
    mirrored = relation(
        from_control_id=2, to_control_id=1,
        from_quote="follow the approved implementation plan",
        to_quote="approved by the CAB",
    )
    assert await check.check({"relations": [mirrored]}) is None


async def test_without_candidate_pairs_batch_membership_is_all_that_is_checked():
    """depends_on 是整簇一起判的，簇内任意两条都是合法组合，没有对可核。"""
    check = _pair_validator(allowed_pairs=None)
    crossed = relation(from_control_id=1, to_control_id=4)
    assert await check.check({"relations": [crossed]}) is None
