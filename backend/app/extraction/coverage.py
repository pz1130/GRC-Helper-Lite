"""按当前原文重算输入指纹，只展示仍适用的抽取留痕。

未引用不是漏抽结论；被引用也不是语义完整或人工确认的证明。
"""

from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.extraction.batching import build_batches, render
from app.extraction.models import ExtractionInventory
from app.extraction.prompts import EXTRACT_TASK_KEY
from app.extraction.tasks import CHECKPOINT_KEY, fingerprint
from app.llm.models import LLMCall
from app.llm.routing import routed_model


async def extraction_coverage(session: AsyncSession, document_id: int) -> dict[str, Any]:
    clauses = list(await session.scalars(
        select(Clause).where(Clause.document_id == document_id, Clause.status != "merged")
        .order_by(Clause.order_index, Clause.id)
    ))
    calls = list(await session.scalars(
        select(LLMCall).where(
            LLMCall.task_key == EXTRACT_TASK_KEY, LLMCall.status == "ok",
            LLMCall.redaction_hits.contains({CHECKPOINT_KEY: {"document_id": document_id}}),
        ).order_by(LLMCall.id.desc())
    ))
    by_key = {}
    for call in calls:
        checkpoint = call.redaction_hits[CHECKPOINT_KEY]
        by_key.setdefault(checkpoint["fingerprint"], call)
    models = {call.redaction_hits[CHECKPOINT_KEY].get("input_model") for call in calls}
    current_model = await routed_model(session, EXTRACT_TASK_KEY)
    # 路由已切换时不能让旧模型的成功批次掩盖新模型尚未完成的第二阶段。
    models = {current_model} if current_model is not None else models | {None}
    inventories = list(await session.scalars(select(ExtractionInventory).where(
        ExtractionInventory.document_id == document_id,
    ).order_by(ExtractionInventory.id.desc())))
    by_inventory_key = {}
    for inventory in inventories:
        by_inventory_key.setdefault(inventory.fingerprint, inventory)
    rows = []
    reference_issues = {}
    for batch in build_batches(clauses):
        for ref in batch.references:
            if ref.status != "resolved":
                reference_issues[(ref.source_clause_id, ref.label, ref.status)] = asdict(ref)
        prompt = render(batch)
        matches = [
            by_key[key] for model in models
            if (key := fingerprint(document_id, prompt, None, model=model)) in by_key
        ]
        latest = max(matches, key=lambda call: call.id) if matches else None
        checkpoint = latest.redaction_hits[CHECKPOINT_KEY] if latest else {}
        # 有整批检查点时展示与该批控制点对应的清单；否则展示当前路由的第一阶段。
        inventory = (
            next((entry for entry in inventories if entry.id == checkpoint.get("inventory_id")), None)
            if latest else by_inventory_key.get(
                fingerprint(document_id, prompt, None, model=current_model)
            )
        )
        reviews = {row["clause_id"]: row["status"]
                   for row in inventory.payload["clause_reviews"]} if inventory else {}
        semantic_review = inventory.semantic_review if inventory else None
        findings = semantic_review["findings"] if semantic_review else []
        cited = set(checkpoint.get("cited_target_clause_ids", []))
        for clause in batch.clauses:
            finding_count = sum(clause.id in finding["clause_ids"] for finding in findings)
            rows.append({
                "clause_id": clause.id,
                "citation_label": clause.citation_label,
                "status": "not_completed" if latest is None else (
                    "cited" if clause.id in cited else "uncited"
                ),
                "llm_call_id": latest.id if latest else None,
                "inventory_id": inventory.id if inventory else None,
                "inventory_status": "needs_review" if finding_count else reviews.get(clause.id),
                "semantic_review_status": ("findings" if finding_count else "no_findings")
                if semantic_review else "not_completed",
                "semantic_finding_count": finding_count,
            })
    return {
        "document_id": document_id,
        "counts": {status: sum(row["status"] == status for row in rows)
                   for status in ("cited", "uncited", "not_completed")},
        "clauses": rows,
        "reference_issues": list(reference_issues.values()),
    }
