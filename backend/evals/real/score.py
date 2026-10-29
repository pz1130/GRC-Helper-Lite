"""真实语料评分：沿用合成语料的同一套打分（evals/segmentation/score.py: score_doc）。

    python evals/real/score.py <out.json>                       # 用当前 PYTHONPATH 里的 app 解析
    PYTHONPATH=<main 工作树>/backend python <本文件> before.json  # 对比基线

本文件按绝对路径加载打分函数，所以可以拿去给旧版本解析器打分。
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("seg_score", HERE.parent / "segmentation" / "score.py")
seg = importlib.util.module_from_spec(spec)
sys.modules["seg_score"] = seg
spec.loader.exec_module(seg)


def _parse_path(row):
    p = HERE / row["file"]
    return HERE / "docs" / "converted" / (p.stem + ".docx") if p.suffix == ".doc" else p


def _mean(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def main(out: Path) -> None:
    from app.parsing.registry import get_parser

    with open(HERE / "manifest.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    docs = {}
    for row in rows:
        if not (HERE / "gold" / f"{row['id']}.gold.json").exists():
            print("no gold yet:", row["id"])
            continue
        gold = json.loads((HERE / "gold" / f"{row['id']}.gold.json").read_text(encoding="utf-8"))
        path = _parse_path(row)
        try:
            r = seg.score_doc(gold, get_parser(path).parse(path).clauses)
        except Exception as exc:  # noqa: BLE001
            r = {"error": f"{type(exc).__name__}: {exc}", "score": 0.0, "f1": 0.0, "content": 0.0}
        docs[row["id"]] = r | {"lang": row["lang"], "format": path.suffix[1:], "kind": row["kind"],
                               "reviewed": gold.get("reviewed", False)}
    groups = defaultdict(list)
    for d in docs.values():
        for g in (f"lang:{d['lang']}", f"format:{d['format']}", f"kind:{d['kind']}"):
            groups[g].append(d)
    summary = {"documents": len(docs), "overall": _mean([d["score"] for d in docs.values()]),
               "structure_f1": _mean([d["f1"] for d in docs.values()]),
               "content": _mean([d["content"] for d in docs.values()])}
    summary |= {g: {"n": len(v), "score": _mean([d["score"] for d in v]),
                    "f1": _mean([d["f1"] for d in v]), "content": _mean([d["content"] for d in v])}
                for g, v in sorted(groups.items())}
    out.write_text(json.dumps({"summary": summary, "documents": docs}, ensure_ascii=False, indent=1))
    for k, d in docs.items():
        print(f"{k:22s} {d['score']:.3f} f1={d['f1']:.3f} c={d['content']:.3f} g={d.get('gold')} p={d.get('pred')} {d.get('error','')}")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
