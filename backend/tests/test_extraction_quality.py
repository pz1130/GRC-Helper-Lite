"""按需跑的 M4 评估，打在应用库上：`make extraction-eval`。

只算本次评估产出的提案。关键词分组量的是**词面召回**的近似值，不是语义评判。
准确率要人对着被引用的条款逐条看——这份报告**刻意不去编造它**。"""

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# 同 gold_queries：按某一批具体文档写成，不进版本库。
# 格式见 .example（里面的文档名是假的，换语料就整份重写，不要沿用那些主题）。
GOLD = Path(
    os.environ.get("GOLD_CONTROLS")
    or Path(__file__).parent / "fixtures" / "gold_controls.local.json"
)
TARGET_RECALL = 0.70
TARGET_CITATION_PASS_RATE = 0.90


def matches_groups(text: str, groups: list[list[str]]) -> bool:
    """每一组概念都必须在**同一条**提案里命中。"""
    folded = " ".join(text.casefold().split())
    return bool(groups) and all(
        any(term.casefold() in folded for term in alternatives)
        for alternatives in groups
    )


@pytest.mark.skipif(
    os.environ.get("RUN_EXTRACTION_EVAL") != "1" or not GOLD.exists(),
    reason="需显式开启真实模型验收，并在本地放一份黄金集，见 make extraction-eval",
)
@pytest.mark.asyncio
async def test_extraction_quality_on_application_corpus(capsys: Any) -> None:
    import app.models  # noqa: F401
    from app.extraction.citations import ClauseCitationValidator
    from app.extraction.tasks import run_extraction
    from app.ingest.models import DocStatus, Document
    from app.llm.routing import resolve
    from app.review.models import Proposal, ProposalKind

    app_url = os.environ.get("APP_DATABASE_URL")
    assert app_url, "APP_DATABASE_URL 必须显式指定应用库；不能使用测试库"
    assert app_url != os.environ.get("TEST_DATABASE_URL"), "验收不能打测试库"
    engine = create_async_engine(app_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cases = json.loads(GOLD.read_text(encoding="utf-8"))
    reports: list[dict[str, Any]] = []
    failures: list[str] = []

    try:
        async with factory() as session:
            # Resolve all prerequisites before spending any tokens.
            provider, _ = await resolve(session, "control_extract")
            documents = []
            for case in cases:
                candidates = list(await session.scalars(
                    select(Document).where(
                        Document.title.ilike(f"%{case['document']}%"),
                        Document.status == DocStatus.ACTIVE,
                    ).order_by(Document.id)
                ))
                assert len(candidates) == 1, (
                    f"{case['document']}: 需要唯一 active 文档，实际 {len(candidates)}"
                )
                assert len(case["keyword_groups"]) == len(case["must_find"])
                documents.append(candidates[0])

            for case, document in zip(cases, documents, strict=True):
                result = await run_extraction(
                    session, document.id, run_key=f"quality-eval:{uuid4()}"
                )
                await session.commit()
                proposals = list(await session.scalars(
                    select(Proposal).where(
                        Proposal.id.in_(result["proposal_ids"]),
                        Proposal.document_id == document.id,
                        Proposal.kind == ProposalKind.CONTROL_EXTRACT,
                    ).order_by(Proposal.id)
                ))
                texts = [f"{p.payload.get('title', '')} {p.payload.get('statement', '')}"
                         for p in proposals]
                matched = [
                    any(matches_groups(text, groups) for text in texts)
                    for groups in case["keyword_groups"]
                ]
                validator = ClauseCitationValidator(session, document_id=document.id)
                bad_citations = []
                for proposal in proposals:
                    reason = await validator.check({"controls": [proposal.payload]})
                    if reason:
                        bad_citations.append({"proposal_id": proposal.id, "reason": reason})
                if not result["batches"]:
                    failures.append(f"{case['document']}: 没有抽取批次")
                if len(proposals) < case["min_controls"]:
                    failures.append(
                        f"{case['document']}: {len(proposals)} 条，低于 {case['min_controls']}"
                    )
                if bad_citations:
                    failures.append(f"{case['document']}: 引用事后复核失败 {bad_citations}")
                reports.append({
                    "document": document.title,
                    "document_id": document.id,
                    "batches": result["batches"],
                    "rejected": result["rejected"],
                    "controls": len(proposals),
                    "expected": case["must_find"],
                    "matched": matched,
                    "bad_citations": bad_citations,
                    "proposals": [{"id": p.id, "payload": p.payload,
                                   "citations": p.citations} for p in proposals],
                })

            expected = sum(len(r["matched"]) for r in reports)
            recall = sum(sum(r["matched"]) for r in reports) / expected
            batches = sum(r["batches"] for r in reports)
            pass_rate = 1 - sum(r["rejected"] for r in reports) / batches if batches else 0.0
            if recall < TARGET_RECALL:
                failures.append(f"关键控制点词组召回率 {recall:.2f} < {TARGET_RECALL}")
            if pass_rate < TARGET_CITATION_PASS_RATE:
                failures.append(f"批次校验通过率 {pass_rate:.2f} < {TARGET_CITATION_PASS_RATE}")
            report = {
                "model": provider.model,
                "keyword_recall": recall,
                "batch_validation_pass_rate": pass_rate,
                "precision": None,
                "precision_note": "需要人工逐条判断是否为可审计的真实控制点",
                "documents": reports,
                "failures": failures,
            }
            # 报告路径要显式给出；制度原文一个字都不进版本库。
            if report_path := os.environ.get("EXTRACTION_REPORT_PATH"):
                Path(report_path).write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            with capsys.disabled():
                for item in reports:
                    print(f"{item['document']}: {item['controls']} controls, "
                          f"recall {sum(item['matched'])}/{len(item['matched'])}, "
                          f"rejected {item['rejected']}/{item['batches']}")
                    for description, matched in zip(item["expected"], item["matched"], strict=True):
                        if not matched:
                            print(f"  未命中词组，待原文核对: {description}")
                print(f"Keyword recall={recall:.2f}; batch validation pass={pass_rate:.2f}")
                print("Precision: pending human review (not inferred from keyword matches)")
            assert not failures, "；".join(failures)
    finally:
        await engine.dispose()
