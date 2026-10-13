from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.models  # noqa: F401  —— AuditLog 的关系需要全部模型已注册
from app.extraction.citations import ClauseCitationValidator
from app.llm.citation_repair import repair_quote

SOURCE = (
    "Access to privileged accounts shall be reviewed at least every 90 days by the "
    "system owner, and the results shall be recorded in the access register."
)


@pytest.mark.parametrize("quote", [
    pytest.param("access to privileged accounts shall be reviewed at least every 90 days",
                 id="case"),
    pytest.param("Access to privileged accounts shall be reviewed at least every 90 days by system owner",
                 id="dropped-article"),
    pytest.param("Access to privileged acounts shall be reviewed at least every 90 days",
                 id="typo"),
    pytest.param("the results shall be recorded in the access register",
                 id="already-verbatim-is-not-a-repair"),
])
def test_copy_errors_are_repaired_to_the_source_span(quote):
    fixed = repair_quote(quote, SOURCE)
    if quote in SOURCE:
        assert fixed is None
        return
    assert fixed is not None
    assert fixed.quote in SOURCE


@pytest.mark.parametrize("quote", [
    pytest.param("Access to privileged accounts may be reviewed at least every 90 days", id="modal"),
    pytest.param("Access to privileged accounts shall be reviewed at least every 30 days", id="number"),
    pytest.param("Access to privileged accounts shall not be reviewed at least every 90 days",
                 id="negation"),
    pytest.param("Access to accounts shall be reviewed at least every 90 days", id="dropped-qualifier"),
    pytest.param("Access to privileged accounts shall be audited at least every 90 days",
                 id="different-word"),
    pytest.param("Users must store secrets in the approved vault service only", id="fabricated"),
    pytest.param("privileged accounts reviewed", id="too-short"),
])
def test_meaning_changes_are_never_repaired(quote):
    assert repair_quote(quote, SOURCE) is None


async def test_validator_repairs_in_place_and_audits():
    session = SimpleNamespace(
        scalars=AsyncMock(return_value=[SimpleNamespace(id=1, document_id=10, heading="", text=SOURCE)]),
        add=lambda entry: added.append(entry),
    )
    added: list = []
    validator = ClauseCitationValidator(session, document_id=10, clause_ids={1})
    data = {"controls": [{"citations": [{
        "clause_id": 1,
        "quote": "access to privileged accounts shall be reviewed at least every 90 days",
    }]}]}
    assert await validator.check(data) is None
    assert data["controls"][0]["citations"][0]["quote"] in SOURCE
    assert len(validator.repairs) == 1
    assert added and added[0].action == "citation.repair"
