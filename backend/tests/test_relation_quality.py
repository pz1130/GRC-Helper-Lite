"""Opt-in M6 relation review against the application database: make relation-eval.

不设自动质量指标：关系对错是语义判断，没有 OLIR 那样的权威外部标尺。
M4 用词组召回、M5 用官方交叉映射，都是因为恰好存在可用基准；这里没有。
强造一个只会得到又一个测不了的闸门。
"""

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TARGET_CITATION_PASS_RATE = 0.90


@pytest.mark.skipif(
    os.environ.get("RUN_RELATION_EVAL") != "1",
    reason="需显式开启真实模型抽查，见 make relation-eval",
)
@pytest.mark.asyncio
async def test_relation_inference_produces_reviewable_output(capsys: Any) -> None:
    import app.models  # noqa: F401
    from app.controls.models import Control
    from app.llm.routing import resolve
    from app.relations.indexing import embed_pending
    from app.relations.tasks import run_inference
    from app.review.models import Proposal, ProposalKind

    app_url = os.environ.get("APP_DATABASE_URL")
    assert app_url, "APP_DATABASE_URL 必须显式指定应用库；不能使用测试库"
    assert app_url != os.environ.get("TEST_DATABASE_URL"), "抽查不能打测试库"

    engine = create_async_engine(app_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            provider, _ = await resolve(session, "relation_inference")
            controls = {c.id: c for c in await session.scalars(select(Control))}
            assert controls, "控制点库为空；请先在确认队列中确认控制点"

            # 循环到补完为止：embed_pending 的默认 limit 是给同步 HTTP 端点定的
            # （两批往返），一次调用只覆盖前 128 个控制点。少了向量的控制点不会
            # 报错，只会静默缺席 duplicates 通道——136 个里少 8 个，闸门就是在
            # 不完整的语料上测的。
            indexed = {"embedded": 0, "model": None, "pending": None}
            while True:
                round_ = await embed_pending(session)
                await session.commit()
                indexed["embedded"] += round_["embedded"]
                indexed["model"] = round_["model"]
                indexed["pending"] = round_["pending"]
                if not round_["embedded"] or not round_["pending"]:
                    break
            assert indexed["pending"] == 0, (
                f"仍有 {indexed['pending']} 个控制点没有向量，duplicates 通道会缺席它们")

            summary = await run_inference(session, run_key=f"relation-eval:{uuid4()}")
            await session.commit()

            proposals = list(await session.scalars(
                select(Proposal).where(
                    Proposal.id.in_(summary["proposal_ids"]),
                    Proposal.kind == ProposalKind.RELATION,
                )
            ))
            # 两个完全不同的量，别再混成一个。
            # · 批次校验通过率（= M5 的 batch_validation_pass_rate）：模型有没有
            #   吐出 schema 合法的 JSON。供应商故障不是质量信号，剔出分母。
            # · 闸 4 通过率：吐出来的关系里，两端引文都逐字对得上的比例。闸 4 的
            #   失败在 run_inference 里是逐条丢弃的，永远不会计进 rejected——
            #   拿批次率冒充闸 4 率，等于这个门按它自己写的理由永远失败不了。
            judged_batches = summary["attempted_batches"] - summary["failed"]
            batch_pass_rate = (
                (judged_batches - summary["rejected"]) / judged_batches
                if judged_batches else 0.0
            )
            kept = summary["proposals"]
            judged_relations = kept + summary["dropped_relations"]
            gate4_pass_rate = kept / judged_relations if judged_relations else 0.0

            by_type: dict[str, list[dict[str, Any]]] = {}
            for proposal in proposals:
                payload = proposal.payload
                left = controls[payload["from_control_id"]]
                right = controls[payload["to_control_id"]]
                by_type.setdefault(payload["relation_type"], []).append({
                    "proposal_id": proposal.id,
                    "from": f"{left.code} {left.title}",
                    "to": f"{right.code} {right.title}",
                    "from_quote": payload["from_quote"],
                    "to_quote": payload["to_quote"],
                    "rationale": payload["rationale"],
                    "confidence": payload["confidence"],
                })

            report = {
                "model": provider.model,
                "controls": len(controls),
                "embedded": indexed["embedded"],
                "embeddings_pending": indexed["pending"],
                "batches": summary["batches"],
                "rejected": summary["rejected"],
                "failed": summary["failed"],
                "dropped_relations": summary["dropped_relations"],
                "duplicate_relations": summary["duplicate_relations"],
                "batch_validation_pass_rate": batch_pass_rate,
                "gate4_citation_pass_rate": gate4_pass_rate,
                "judged_relations": judged_relations,
                "relations": {k: len(v) for k, v in by_type.items()},
                "samples": by_type,
                "precision": None,
                "precision_note": "需人工逐条判断关系是否成立；本脚本不推断该值",
            }
            if report_path := os.environ.get("RELATION_REPORT_PATH"):
                Path(report_path).write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

            with capsys.disabled():
                print(f"model={provider.model} controls={len(controls)} "
                      f"batches={summary['batches']} "
                      f"（向量补算 {indexed['embedded']}，剩余 {indexed['pending']}）")
                print(f"批次校验通过率 = {batch_pass_rate:.2f}  "
                      f"（rejected {summary['rejected']} / failed {summary['failed']}）")
                print(f"闸 4 通过率 = {gate4_pass_rate:.2f}  "
                      f"（保留 {kept} / 逐条丢弃 {summary['dropped_relations']} / "
                      f"本轮重复跳过 {summary['duplicate_relations']}）")
                for relation_type, rows in by_type.items():
                    print(f"{relation_type}: {len(rows)} 条")
                print("precision: pending human review（不由脚本推断）")

            # 一条关系都没产出时闸 4 无从谈起。这不是引文问题，但也不该被当成
            # 通过悄悄放行——整轮空手而归本身就是要看的结果。
            assert judged_relations > 0, (
                f"整轮没有产出任何关系（{summary['batches']} 批，rejected "
                f"{summary['rejected']}，failed {summary['failed']}），闸 4 无从评估")
            assert gate4_pass_rate >= TARGET_CITATION_PASS_RATE, (
                f"闸 4 通过率 {gate4_pass_rate:.2f} < {TARGET_CITATION_PASS_RATE}")
    finally:
        await engine.dispose()
