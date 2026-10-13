"""清单守恒、引用完整性、分阶段恢复及只读核对。"""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.extraction import inventory as module
from app.extraction import tasks
from app.extraction.coverage import extraction_coverage
from app.extraction.inventory import (
    COMPONENTS,
    INVENTORY_SCHEMA,
    NORMALIZE_SCHEMA,
    InventoryValidator,
    NormalizationValidator,
)
from app.extraction.models import ExtractionInventory
from app.extraction.semantic_review import REVIEW_SCHEMA
from app.llm.models import LLMCall, RulesetName
from app.llm.runner import ValidatedResult
from app.llm.validation import ValidationFailure, validate
from app.review.models import Proposal
from tests.test_extraction_tasks_db import extraction_document as document_fixture

extraction_document = document_fixture


def requirement(cid=1, rid="R1"):
    return {
        "id": rid, "title": "Dual approval", "statement": "Two approvers are required.",
        "target_clause_ids": [cid], "confidence": 0.9,
        "citations": [{"clause_id": cid, "quote": "Two approvers are required."}],
        "components": {**dict.fromkeys(COMPONENTS), "action": "are required"},
    }


def inventory(cid=1):
    return {"requirements": [requirement(cid)], "insufficient_evidence": False,
            "clause_reviews": [{"clause_id": cid, "status": "requirements"}]}


def control(req):
    return {**{k: req[k] for k in ("title", "statement", "confidence", "citations")},
            "requirement_ids": [req["id"]]}


@pytest.mark.parametrize("change, message", [
    (lambda p: p["requirements"].append(deepcopy(p["requirements"][0])), "unique"),
    (lambda p: p.update(clause_reviews=[]), "every target"),
    (lambda p: p["clause_reviews"].append({"clause_id": 9, "status": "no_requirement"}), "every target"),
    (lambda p: p["clause_reviews"][0].update(status="no_requirement"), "origin"),
    (lambda p: p["requirements"][0]["components"].update(frequency="monthly"), "copied"),
    (lambda p: p["requirements"][0].update(target_clause_ids=[9]), "target clauses"),
])
async def test_inventory_rejects_missing_reviews_invented_fields_and_invalid_origins(change, message):
    payload = inventory()
    change(payload)
    validator = InventoryValidator(AsyncMock(check=AsyncMock(return_value=None)), [1])
    assert message in await validator.check(payload)


async def test_definition_can_be_cited_as_context_without_becoming_a_requirement():
    payload = inventory()
    payload["clause_reviews"].append({"clause_id": 2, "status": "no_requirement"})
    payload["requirements"][0]["citations"].append({"clause_id": 2, "quote": "Production only"})
    payload["requirements"][0]["components"]["scope"] = "Production only"
    validator = InventoryValidator(AsyncMock(check=AsyncMock(return_value=None)), [1, 2])
    assert await validator.check(payload) is None


@pytest.mark.parametrize("ids", [[], ["R2"], ["R1", "R2"], ["R1", "R1"]])
async def test_second_stage_rejects_lost_new_or_duplicated_requirement_ids(ids):
    req = requirement()
    validator = NormalizationValidator(AsyncMock(check=AsyncMock(return_value=None)), [req])
    assert "exactly one" in await validator.check({"controls": [{**control(req), "requirement_ids": ids}]})


async def test_second_stage_cannot_drop_a_qualifier_quote():
    req = requirement()
    req["citations"].append({"clause_id": 2, "quote": "Except emergency access"})
    item = control(deepcopy(req))
    item["citations"].pop()
    validator = NormalizationValidator(AsyncMock(check=AsyncMock(return_value=None)), [req])
    assert "retain supporting quote" in await validator.check({"controls": [item]})


def test_schemas_require_structured_components_and_inventory_mapping():
    data = inventory()
    validate(data, INVENTORY_SCHEMA)
    validate({"controls": [control(data["requirements"][0])]}, NORMALIZE_SCHEMA)
    data["requirements"][0]["components"].pop("exceptions")
    with pytest.raises(ValidationFailure):
        validate(data, INVENTORY_SCHEMA)


def install_runner(monkeypatch, cid, *, second_failure=None, empty=False,
                   review_payload=None, review_failure=None):
    calls = []

    async def runner(session, **kwargs):
        first = kwargs["schema"] is INVENTORY_SCHEMA
        review = kwargs["schema"] is REVIEW_SCHEMA
        calls.append("inventory" if first else "review" if review else "normalization")
        if review and review_failure:
            raise review_failure
        if not first and not review and second_failure:
            raise second_failure
        data = inventory(cid) if first else {"controls": [control(requirement(cid))]}
        if empty and first:
            data = {"requirements": [], "insufficient_evidence": True,
                    "clause_reviews": [{"clause_id": cid, "status": "needs_review"}]}
        if review:
            data = review_payload or {"checked_clause_ids": [cid], "findings": []}
        validate(data, kwargs["schema"])
        reason = await kwargs["citation_validator"].check(data)
        if reason:
            raise ValidationFailure(reason)
        call = LLMCall(task_key="control_extract", model="test", prompt_hash="b" * 64,
                       ruleset=RulesetName.GENERATION, status="ok", redaction_hits={})
        session.add(call)
        await session.flush()
        return ValidatedResult(data, None, call.id)

    monkeypatch.setattr(module, "run", runner)
    return calls


async def test_real_pipeline_saves_inventory_maps_proposals_and_resumes(
    db_session, extraction_document, monkeypatch,
):
    doc, clause = extraction_document
    calls = install_runner(monkeypatch, clause.id)
    result = await tasks.run_extraction(db_session, doc.id)
    assert calls == ["inventory", "normalization", "review"]
    saved = await db_session.scalar(select(ExtractionInventory))
    proposal = await db_session.get(Proposal, result["proposal_ids"][0])
    assert "requirement_ids" not in proposal.payload
    assert saved.payload["requirements"][0]["target_clause_ids"] == [clause.id]
    final_call = await db_session.get(LLMCall, proposal.llm_call_id)
    assert final_call.redaction_hits[tasks.CHECKPOINT_KEY]["inventory_id"] == saved.id
    assert final_call.redaction_hits[tasks.CHECKPOINT_KEY]["requirement_mappings"] == [["R1"]]
    assert (await tasks.run_extraction(db_session, doc.id))["skipped_batches"] == 1
    assert len(calls) == 3
    coverage = await extraction_coverage(db_session, doc.id)
    assert coverage["clauses"][0]["inventory_id"] == saved.id
    clause.text += " Retain records."
    await db_session.flush()
    assert (await extraction_coverage(db_session, doc.id))["clauses"][0]["inventory_id"] is None


async def test_second_stage_failure_keeps_inventory_but_not_completion(
    db_session, extraction_document, monkeypatch,
):
    doc, clause = extraction_document
    calls = install_runner(monkeypatch, clause.id, second_failure=ValidationFailure("Missing R1"))
    result = await tasks.run_extraction(db_session, doc.id)
    assert result["rejected"] == 1 and result["completed_batches"] == 0
    assert await db_session.scalar(select(func.count()).select_from(ExtractionInventory)) == 1
    report = await extraction_coverage(db_session, doc.id)
    assert report["clauses"][0]["status"] == "not_completed"
    assert report["clauses"][0]["inventory_status"] == "requirements"
    assert calls == ["inventory", "normalization"]
    calls = install_runner(monkeypatch, clause.id)
    assert (await tasks.run_extraction(db_session, doc.id))["completed_batches"] == 1
    assert calls == ["normalization", "review"]


async def test_empty_uncertain_inventory_is_visible_and_skips_second_stage(
    db_session, extraction_document, monkeypatch,
):
    doc, clause = extraction_document
    calls = install_runner(monkeypatch, clause.id, empty=True)
    result = await tasks.run_extraction(db_session, doc.id)
    assert calls == ["inventory", "review"] and result["proposals"] == 0
    report = await extraction_coverage(db_session, doc.id)
    assert report["clauses"][0]["inventory_status"] == "needs_review"
    assert report["clauses"][0]["status"] == "uncited"


async def test_inventory_api_auth_document_boundary_and_read_access(
    client, db_session, extraction_document, monkeypatch,
):
    from app.iam.permissions import Role
    from tests.test_clause_edit import _user

    doc, clause = extraction_document
    install_runner(monkeypatch, clause.id)
    await tasks.run_extraction(db_session, doc.id)
    saved = await db_session.scalar(select(ExtractionInventory))
    path = f"/api/extraction/documents/{doc.id}/inventories/{saved.id}"
    assert (await client.get(path)).status_code == 401
    user = await _user(db_session, Role.VIEWER)
    login = await client.post("/api/auth/login", json={"email": user.email, "password": "pw123456"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = await client.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json()["requirements"][0]["id"] == "R1"
    other = f"/api/extraction/documents/{doc.id + 1}/inventories/{saved.id}"
    assert (await client.get(other, headers=headers)).status_code == 404


async def test_budget_pause_between_stages_preserves_inventory(db_session, extraction_document, monkeypatch):
    from app.llm.budget import BudgetExceeded

    doc, clause = extraction_document
    install_runner(monkeypatch, clause.id, second_failure=BudgetExceeded("Monthly budget exhausted"))
    result = await tasks.run_extraction(db_session, doc.id)
    assert result["paused_reason"] == "budget.exhausted"
    assert result["completed_batches"] == 0
    assert await db_session.scalar(select(func.count()).select_from(ExtractionInventory)) == 1
    calls = install_runner(monkeypatch, clause.id)
    resumed = await tasks.run_extraction(db_session, doc.id)
    assert calls == ["normalization", "review"]
    assert len(resumed["llm_call_ids"]) == 2


async def test_first_stage_fabricated_quote_never_creates_inventory(db_session, extraction_document, monkeypatch):
    doc, clause = extraction_document

    async def bad_runner(session, **kwargs):
        data = inventory(clause.id)
        data["requirements"][0]["citations"][0]["quote"] = "A fabricated requirement."
        reason = await kwargs["citation_validator"].check(data)
        assert reason is not None
        raise ValidationFailure(reason)

    monkeypatch.setattr(module, "run", bad_runner)
    result = await tasks.run_extraction(db_session, doc.id)
    assert result["rejected"] == 1
    assert await db_session.scalar(select(func.count()).select_from(ExtractionInventory)) == 0


def test_inventory_prompt_and_schema_change_invalidate_checkpoint(monkeypatch):
    before = tasks.fingerprint(1, "text", None)
    monkeypatch.setattr(tasks, "INVENTORY_SYSTEM", "different inventory contract")
    assert before != tasks.fingerprint(1, "text", None)


async def test_old_model_completion_does_not_hide_new_model_partial_inventory(
    db_session, extraction_document, monkeypatch,
):
    from app.extraction import coverage

    doc, clause = extraction_document
    route = AsyncMock(return_value="model-a")
    monkeypatch.setattr(tasks, "routed_model", route)
    monkeypatch.setattr(coverage, "routed_model", route)
    install_runner(monkeypatch, clause.id)
    await tasks.run_extraction(db_session, doc.id)
    assert (await extraction_coverage(db_session, doc.id))["clauses"][0]["status"] == "cited"
    route.return_value = "model-b"
    install_runner(monkeypatch, clause.id, second_failure=ValidationFailure("Missing R1"))
    await tasks.run_extraction(db_session, doc.id)
    report = await extraction_coverage(db_session, doc.id)
    latest = await db_session.scalar(select(ExtractionInventory).order_by(ExtractionInventory.id.desc()))
    assert report["clauses"][0]["status"] == "not_completed"
    assert report["clauses"][0]["inventory_id"] == latest.id
