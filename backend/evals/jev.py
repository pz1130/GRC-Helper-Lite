"""Jev（TypeSafe 判断模型）评估：候选预筛与映射后复核（K17）。

只发公开数据（NIST CSF 2.0 / 800-53 Rev.5）。key 读 JEV_API_KEY（缺省回退 TYPESAFE_API_KEY），不落盘。
请求格式与 app/decision/jev.py 相同：一次调用 = 一个框架项作 state，
每个控制点一道 noul 题（概率 0..1）。

    python -m evals.jev score            # 106 项 × 191 控制点打分，缓存到 .cache/
    python -m evals.jev prefilter        # 前 k 候选召回
    python -m evals.jev verify --db grc_eval_mapping_after-chunked
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

from evals import corpus, metrics

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache" / "jev-scores.json"
RESULTS = HERE / "results"
MODEL = os.environ.get("GRC_EVAL_JEV_MODEL", "jev-1.13.0")
URL = os.environ.get("GRC_EVAL_JEV_URL", "https://api.typesafe.ai/v1") + "/systemone"
INSTRUCTION = (
    "The state is a requirement from an external cybersecurity framework. Does the following "
    "internal control, if operated, satisfy or directly contribute to meeting that requirement? "
    "Control: {control}"
)


def _items() -> list[tuple[str, str]]:
    csf = {row["code"]: row for row in corpus._rows("nist-csf-2.0.csv")}
    return [(c, f"{c} {csf[c]['title']}: {csf[c]['description']}") for c in sorted(corpus.olir_pairs())]


def _ask(client: httpx.Client, state: str, controls: dict[str, str]) -> tuple[dict[str, float], int]:
    key = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY") or sys.exit("需要 JEV_API_KEY")
    ids = {f"q{n}": code for n, code in enumerate(controls)}
    body = {"model": MODEL, "state": f"Framework requirement: {state}",
            "questions": {q: {"type": "noul", "instructions": INSTRUCTION.format(control=controls[c])}
                          for q, c in ids.items()}}
    for attempt in range(4):
        resp = client.post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=120)
        if resp.status_code == 200:
            data = resp.json()
            usage = data["usage"]["input_tokens"] + data["usage"]["output_tokens"]
            return {ids[q]: float(a["noul"]) for q, a in data["answers"].items()}, usage
        time.sleep(2 ** attempt)
    raise RuntimeError(f"jev http {resp.status_code}")


def _control_texts() -> dict[str, str]:
    return {c["code"]: f"{c['title']} ({c['code']}): {c['statement']}" for c in corpus.mapping_controls()}


def score() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    controls = _control_texts()
    out, tokens, started = {}, 0, time.time()
    with httpx.Client() as client:
        for code, text in _items():
            out[code], used = _ask(client, text, controls)
            tokens += used
    data = {"model": MODEL, "scores": out, "tokens": tokens, "elapsed_s": round(time.time() - started, 1)}
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(data))
    return data


def prefilter() -> dict:
    data = score()
    ranked = {i: [c for c, _ in sorted(s.items(), key=lambda kv: (-kv[1], kv[0]))] for i, s in data["scores"].items()}
    expected = corpus.olir_pairs()
    return {"model": data["model"], "tokens": data["tokens"], "elapsed_s": data["elapsed_s"],
            **{f"recall@{k}": metrics.candidate_recall(ranked, expected, k) for k in (10, 20, 40)}}


def verify(db: str) -> dict:
    """拿已有映射评估库里的提案（MiniMax 产出），用 Jev 复核后按阈值过滤。"""
    import subprocess

    out = subprocess.run(
        ["psql", "-h", "localhost", "-p", os.environ.get("GRC_EVAL_PG_PORT", "55432"), "-U", "grc",
         "-d", db, "-tA", "-F", ",", "-c",
         "select fi.code, c.code from proposals p "
         "join framework_items fi on fi.id = (p.payload->>'framework_item_id')::int "
         "join controls c on c.id = (p.payload->>'control_id')::int "
         "where p.kind = 'mapping'"],
        capture_output=True, text=True, check=True, env={**os.environ, "PGPASSWORD": "grc"},
    ).stdout
    rows = [line.split(",") for line in out.splitlines() if line]
    proposals = {(i, metrics.base_control(c)) for i, c in rows}
    expected = corpus.olir_pairs()
    gold = {(i, c) for i, cs in expected.items() for c in cs}
    scored = {p for p in proposals if p[0] in expected}

    texts, items = _control_texts(), dict(_items())
    by_item: dict[str, list[str]] = {}
    for i, c in sorted(scored):
        by_item.setdefault(i, []).append(c)
    probs, tokens = {}, 0
    with httpx.Client() as client:
        for i, codes in by_item.items():
            answers, used = _ask(client, items[i], {c: texts[c] for c in codes})
            tokens += used
            probs.update({(i, c): p for c, p in answers.items()})
    report = {"model": MODEL, "source_db": db, "tokens": tokens, "unfiltered": metrics.prf(scored, gold)}
    for t in (0.2, 0.3, 0.5, 0.7):
        report[f"threshold_{t}"] = metrics.prf({p for p in scored if probs[p] >= t}, gold)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["score", "prefilter", "verify"])
    parser.add_argument("--db", default="grc_eval_mapping_after-chunked")
    args = parser.parse_args()
    if args.cmd == "score":
        report = {k: v for k, v in score().items() if k != "scores"}
    elif args.cmd == "prefilter":
        report = prefilter()
    else:
        report = verify(args.db)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"jev-{args.cmd}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
