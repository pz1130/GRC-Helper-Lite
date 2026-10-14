"""可复现的评估入口（K15/K16/G4）。

    # 离线、确定性、零成本：映射预筛的候选召回（关键词一路）
    python -m evals.harness prefilter
    # 加上向量一路（需要 embedding key，见下）
    python -m evals.harness prefilter --vectors
    # 真实模型：映射端到端 / 抽取端到端
    python -m evals.harness mapping    --label after
    python -m evals.harness extraction --label after [--retries 3]

真实模型需要：
    GRC_EVAL_LLM_API_KEY   MiniMax（中国站 api.minimaxi.com）key，**只从环境变量读，不落盘**
    GRC_EVAL_CHAT_MODEL    默认 MiniMax-M3
    GRC_EVAL_EMBED_MODEL   默认 embo-01
    GRC_EVAL_DB_ADMIN_URL  默认 postgresql+asyncpg://grc:grc@localhost:55432/postgres
每次运行新建一个独立库（grc_eval_<suite>_<label>），跑完保留以便复查；结果写进
evals/results/<suite>-<label>.json。要对比改动前后，在旧代码的工作树里用同一个
harness 跑（PYTHONPATH 指向旧代码的 backend/）：harness 只用 run_parse /
run_extraction / run_mapping 这几个稳定入口。
"""

import argparse
import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ADMIN_URL = os.environ.get(
    "GRC_EVAL_DB_ADMIN_URL", "postgresql+asyncpg://grc:grc@localhost:55432/postgres"
)
CHAT_MODEL = os.environ.get("GRC_EVAL_CHAT_MODEL", "MiniMax-M3")
EMBED_MODEL = os.environ.get("GRC_EVAL_EMBED_MODEL", "embo-01")
MAX_TOKENS = int(os.environ.get("GRC_EVAL_MAX_TOKENS", "16384"))
CHAT_TASKS = ("control_extract", "framework_mapping")


def _api_key() -> str:
    key = os.environ.get("GRC_EVAL_LLM_API_KEY", "")
    if not key:
        sys.exit("需要环境变量 GRC_EVAL_LLM_API_KEY（真实模型评估）；离线部分用 `prefilter`。")
    return key


def _configure_env(db_name: str) -> str:
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + db_name
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("APP_SECRET_KEY", Fernet.generate_key().decode())
    os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(48))
    return url


async def _fresh_db(db_name: str, url: str) -> None:
    from alembic.config import Config
    from sqlalchemy.ext.asyncio import create_async_engine

    from alembic import command

    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{db_name}"')
        await conn.exec_driver_sql(f'CREATE DATABASE "{db_name}"')
    await admin.dispose()
    cfg = Config(str(HERE.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(HERE.parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, cfg, "head")


async def _providers(session: Any, key: str) -> None:
    from app.crypto import encrypt
    from app.llm.models import AppSetting, LLMProviderConfig, ProviderKind, TaskRouting

    chat = LLMProviderConfig(name="eval-chat", kind=ProviderKind.MINIMAX, model=CHAT_MODEL,
                             api_key_encrypted=encrypt(key))
    emb = LLMProviderConfig(name="eval-embed", kind=ProviderKind.MINIMAX, model=EMBED_MODEL,
                            api_key_encrypted=encrypt(key))
    session.add_all([chat, emb])
    await session.flush()
    for task in CHAT_TASKS:
        session.add(TaskRouting(task_key=task, provider_config_id=chat.id, max_tokens=MAX_TOKENS))
    session.add(TaskRouting(task_key="embedding", provider_config_id=emb.id))
    # 评估不受部署方月度预算约束（预算闸门照常运行，只是上限给足）
    setting = await session.get(AppSetting, "monthly_budget_usd")
    if setting is not None:
        setting.value = {"value": 100000}
    await session.commit()


async def _usage(session: Any) -> dict[str, Any]:
    from sqlalchemy import func, select

    from app.llm.models import LLMCall

    rows = (await session.execute(
        select(LLMCall.task_key, LLMCall.status, func.count(), func.sum(LLMCall.tokens_in),
               func.sum(LLMCall.tokens_out))
        .group_by(LLMCall.task_key, LLMCall.status)
    )).all()
    return {f"{task}:{status}": {"calls": n, "tokens_in": int(ti or 0), "tokens_out": int(to or 0)}
            for task, status, n, ti, to in rows}


async def _repairs(session: Any) -> int:
    from sqlalchemy import func, select

    from app.iam.models import AuditLog

    return int(await session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "citation.repair")
    ) or 0)


# ── 预筛（离线） ──────────────────────────────────────────────

def _embed_direct(texts: list[str], purpose: str) -> list[list[float]]:
    """直连 MiniMax embedding（只给离线预筛用；端到端评估走应用自己的 provider）。"""
    import httpx

    vectors: list[list[float]] = []
    for start in range(0, len(texts), 32):
        resp = httpx.post(
            "https://api.minimaxi.com/v1/embeddings",
            headers={"Authorization": f"Bearer {_api_key()}"},
            json={"model": EMBED_MODEL, "texts": texts[start:start + 32],
                  "type": "query" if purpose == "query" else "db"},
            timeout=60,
        )
        resp.raise_for_status()
        body = resp.json()
        if (body.get("base_resp") or {}).get("status_code", 0) != 0:
            raise RuntimeError(f"embedding error: {body.get('base_resp')}")
        vectors.extend(body["vectors"])
    return vectors


def run_prefilter(args: argparse.Namespace) -> dict[str, Any]:
    from types import SimpleNamespace

    from app.mapping.candidates import control_text, rank_candidates
    from evals import corpus, metrics

    expected = corpus.olir_pairs()
    csf = {row["code"]: row for row in corpus._rows("nist-csf-2.0.csv")}
    items = [(code, f"{csf[code]['title']} {csf[code]['description']}") for code in sorted(expected)]
    controls = corpus.mapping_controls()
    ctl_docs = [(c["code"], control_text(SimpleNamespace(**c))) for c in controls]

    item_vecs = ctl_vecs = None
    if args.vectors:
        cache = HERE / ".cache" / f"vectors-{EMBED_MODEL}.json"
        if cache.exists():
            cached = json.loads(cache.read_text())
        else:
            cached = {
                "items": dict(zip([i for i, _ in items], _embed_direct([t for _, t in items], "query"), strict=True)),
                # 与 relations/indexing.render_control 一致：控制点向量取陈述
                "controls": dict(zip([c["code"] for c in controls],
                                     _embed_direct([c["statement"] or c["title"] for c in controls], "db"),
                                     strict=True)),
            }
            cache.parent.mkdir(exist_ok=True)
            cache.write_text(json.dumps(cached))
        item_vecs, ctl_vecs = cached["items"], cached["controls"]

    report: dict[str, Any] = {"items": len(items), "controls": len(controls),
                              "positives": sum(len(v) for v in expected.values())}
    for name, iv, cv in (("keyword", None, None), ("vector", item_vecs, ctl_vecs), ("hybrid", item_vecs, ctl_vecs)):
        if name != "keyword" and iv is None:
            continue
        if name == "vector":
            from app.mapping.candidates import vector_rank
            ranked = {i: vector_rank(iv[i], cv)[:40] for i, _ in items}
        else:
            ranked = rank_candidates(items, ctl_docs, item_vectors=iv, control_vectors=cv, top_k=40)
        report[name] = {f"recall@{k}": metrics.candidate_recall(ranked, expected, k) for k in (10, 20, 40)}
    return report


# ── 映射（真实模型） ──────────────────────────────────────────

async def run_mapping_eval(args: argparse.Namespace) -> dict[str, Any]:
    db = f"grc_eval_mapping_{args.label}"
    url = _configure_env(db)
    await _fresh_db(db, url)
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.controls.models import Control
    from app.frameworks.models import Framework, FrameworkItem
    from app.review.models import Proposal, ProposalKind
    from evals import corpus, metrics

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await _providers(session, _api_key())
        await _import_csf(session)
        for row in corpus.mapping_controls():
            session.add(Control(code=row["code"], title=row["title"], statement=row["statement"]))
        await session.commit()
        if args.embed_controls:
            from app.relations.indexing import embed_pending
            await embed_pending(session)
            await session.commit()
        framework = await session.scalar(select(Framework).where(Framework.key == "nist-csf-2.0"))

        from app.mapping import tasks as mapping_tasks
        from app.mapping.tasks import run_mapping
        if args.candidates == "jev":
            # 实验：用 Jev 打分（evals/jev.py 的缓存）替换预筛排序，其余流程不变。
            from evals.jev import score

            jev = score()["scores"]
            item_code = {i.id: i.code for i in await session.scalars(
                select(FrameworkItem).where(FrameworkItem.framework_id == framework.id))}
            ctl_id = {c.code: c.id for c in await session.scalars(select(Control))}

            def jev_rank(items, controls, **_):
                out = {}
                for item_id, _text in items:
                    scores = jev.get(item_code[item_id], {})
                    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
                    out[item_id] = [ctl_id[c] for c, _ in ranked]
                return out

            async def no_vectors(*_a, **_k):
                return {}, {}

            mapping_tasks.rank_candidates = jev_rank
            mapping_tasks.load_vectors = no_vectors
        started = time.time()
        summary = await run_mapping(session, framework.id, run_key=f"eval:{args.label}")
        await session.commit()
        elapsed = time.time() - started

        codes = {i.id: i.code for i in await session.scalars(
            select(FrameworkItem).where(FrameworkItem.framework_id == framework.id))}
        ctl = {c.id: c.code for c in await session.scalars(select(Control))}
        proposals = list(await session.scalars(select(Proposal).where(Proposal.kind == ProposalKind.MAPPING)))
        expected = corpus.olir_pairs()
        predicted = {
            (codes[p.payload["framework_item_id"]], metrics.base_control(ctl[p.payload["control_id"]]))
            for p in proposals
        }
        gold = {(csf, c) for csf, cs in expected.items() for c in cs}
        # 只在 OLIR 覆盖的子类上计分
        predicted_scored = {pair for pair in predicted if pair[0] in expected}
        attempted = summary["attempted_batches"]
        report = {
            "batches": summary["batches"], "attempted": attempted,
            "rejected": summary["rejected"], "failed": summary["failed"],
            "dropped_mappings": summary["dropped_mappings"],
            "batch_validation_pass_rate": metrics.rate(attempted - summary["rejected"] - summary["failed"], attempted),
            "candidate_vector_used": summary.get("candidate_vector_used"),
            "mapping": metrics.prf(predicted_scored, gold),
            "citation_repairs": await _repairs(session),
            "proposals": len(proposals),
            "usage": await _usage(session),
            "elapsed_s": round(elapsed, 1),
        }
    await engine.dispose()
    return report


async def _import_csf(session: Any) -> None:
    import csv

    from app.frameworks.importer import import_framework
    from app.iam.models import User
    from app.iam.permissions import Role
    from app.iam.security import hash_password

    actor = User(email="eval@example.com", name="eval", role=Role.ADMIN,
                 password_hash=hash_password(secrets.token_urlsafe(16)))
    session.add(actor)
    await session.flush()
    text = (HERE.parent / "seeds" / "nist-csf-2.0.csv").read_text(encoding="utf-8-sig")
    reader = list(csv.reader(text.splitlines()))
    await import_framework(
        session, reader[0], reader[1:], key="nist-csf-2.0", name_zh="NIST CSF 2.0",
        name_en="NIST CSF 2.0", version="2.0", source="nist.gov", actor=actor,
    )


# ── 抽取（真实模型） ──────────────────────────────────────────

async def run_extraction_eval(args: argparse.Namespace) -> dict[str, Any]:
    db = f"grc_eval_extraction_{args.label}"
    url = _configure_env(db)
    await _fresh_db(db, url)
    import hashlib

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.clauses.models import Clause
    from app.ingest.models import DocType, Document
    from app.ingest.tasks import run_parse
    from app.llm import runner
    from app.llm.validation import clause_source, normalize
    from app.review.models import Proposal, ProposalKind
    from evals import corpus, metrics

    if args.retries is not None:
        runner.MAX_CORRECTION_RETRIES = args.retries
    path = corpus.build_extraction_docx(HERE / ".cache" / "extraction-corpus.docx")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await _providers(session, _api_key())
        data = path.read_bytes()
        doc = Document(title="Eval policy", doc_type=DocType.POLICY, original_filename=path.name,
                       file_hash=hashlib.sha256(data).hexdigest(), file_path=str(path))
        session.add(doc)
        await session.flush()
        parsed = await run_parse(session, doc)
        await session.commit()

        from app.extraction.tasks import run_extraction
        started = time.time()
        summary = await run_extraction(session, doc.id, run_key=f"eval:{args.label}")
        await session.commit()
        elapsed = time.time() - started

        clauses = {c.id: c for c in await session.scalars(select(Clause).where(Clause.document_id == doc.id))}
        proposals = list(await session.scalars(
            select(Proposal).where(Proposal.kind == ProposalKind.CONTROL_EXTRACT)))
        citations = [c for p in proposals for c in (p.citations or [])]
        verbatim = sum(
            1 for c in citations
            if c.get("clause_id") in clauses
            and normalize(c.get("quote", "")) in normalize(clause_source(clauses[c["clause_id"]]))
        )
        attempted = summary["attempted_batches"]
        report = {
            "clauses": parsed["clauses"], "batches": summary["batches"], "attempted": attempted,
            "rejected": summary["rejected"],
            "batch_validation_pass_rate": metrics.rate(attempted - summary["rejected"], attempted),
            "proposals": len(proposals),
            "citations": len(citations),
            "citations_verbatim_after_gate": metrics.rate(verbatim, len(citations)),
            "citation_repairs": await _repairs(session),
            "max_correction_retries": runner.MAX_CORRECTION_RETRIES,
            "usage": await _usage(session),
            "elapsed_s": round(elapsed, 1),
        }
    await engine.dispose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("suite", choices=["prefilter", "mapping", "extraction"])
    parser.add_argument("--label", default="run")
    parser.add_argument("--vectors", action="store_true")
    parser.add_argument("--retries", type=int)
    parser.add_argument("--embed-controls", action="store_true", default=True)
    parser.add_argument("--candidates", choices=["hybrid", "jev"], default="hybrid")
    args = parser.parse_args()

    if args.suite == "prefilter":
        report = run_prefilter(args)
    elif args.suite == "mapping":
        report = asyncio.run(run_mapping_eval(args))
    else:
        report = asyncio.run(run_extraction_eval(args))
    report = {"suite": args.suite, "label": args.label, "chat_model": CHAT_MODEL,
              "embed_model": EMBED_MODEL, "max_tokens": MAX_TOKENS, **report}
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{args.suite}-{args.label}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
