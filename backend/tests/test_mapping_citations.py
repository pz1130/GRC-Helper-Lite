from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.mapping.citations import MappingCitationValidator

ITEM_TEXT = "Identities and credentials are managed – including the “root” account."


def payload(item_id=1, control_id=7, quote="Identities and credentials are managed",
            strength="full"):
    return {"mappings": [{
        "framework_item_id": item_id, "control_id": control_id,
        "strength": strength, "framework_item_quote": quote, "rationale": "r", "confidence": 0.9,
    }]}


def validator(item_ids=frozenset({1, 2})):
    session = SimpleNamespace(scalars=AsyncMock(side_effect=[
        [SimpleNamespace(id=1, description=ITEM_TEXT),
         SimpleNamespace(id=2, description="Other text.")],
        [SimpleNamespace(id=7)],
    ]))
    return MappingCitationValidator(session, item_ids=item_ids)


async def test_a_literal_quote_passes():
    assert await validator().check(payload()) is None


async def test_typographic_variants_are_folded():
    assert await validator().check(payload(quote='including the "root" account.')) is None


@pytest.mark.parametrize("kwargs", [
    {"item_id": 999},
    {"item_id": 2},
    {"control_id": 999},
    {"quote": "Identities are audited"},
    {"quote": "   "},
    {"strength": "maybe"},
    {"item_id": True},
])
async def test_invalid_mapping_rejected(kwargs):
    assert await validator().check(payload(**kwargs))


async def test_empty_list_requires_the_abstention_flag():
    assert await validator().check({"mappings": []})
    assert await validator().check({"mappings": [], "insufficient_evidence": True}) is None


async def test_abstention_cannot_accompany_mappings():
    assert await validator().check({**payload(), "insufficient_evidence": True})


async def test_every_mapping_is_checked_not_just_the_first():
    data = payload()
    data["mappings"].append({
        "framework_item_id": 1, "control_id": 7, "strength": "full",
        "framework_item_quote": "fabricated", "rationale": "r", "confidence": 0.9,
    })
    assert await validator().check(data)
