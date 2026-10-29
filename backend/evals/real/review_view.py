"""人工复核视图：大模型标注 vs 规则标注，逐行列出，附原文行。不看解析器输出。

    python -m evals.real.review_view <doc_id> [--all]
"""
from __future__ import annotations

import json
import sys

from evals.real.extract import lines
from evals.real.label import VWORK, manifest


def show(doc_id: str, full: bool = False) -> None:
    row = next(r for r in manifest() if r["id"] == doc_id)
    ls = lines(row)
    llm = {x["line"]: x for x in json.loads((VWORK / "labels" / f"{doc_id}.llm.json").read_text())}
    rule = {x["line"]: x for x in json.loads((VWORK / "labels" / f"{doc_id}.rule.json").read_text())}
    print(f"# {doc_id} {row['lang']} {row['format']} {row['kind']} lines={len(ls)} llm={len(llm)} rule={len(rule)}")
    for i in sorted(set(llm) | set(rule)):
        a, b = llm.get(i), rule.get(i)
        near = any(j in llm for j in (i - 1, i + 1))
        tag = "  " if a and b else ("L " if a else ("r~" if near else "R "))
        if not full and tag == "  ":
            print(f"{tag}{i:5d} {a['level']} {a['number'][:14]:14s} {a['heading'][:50]}")
            continue
        lab = a or b
        print(f"{tag}{i:5d} {lab['level']} {lab['number'][:14]:14s} {lab['heading'][:50]:50s} | {ls[i][:70]}")


if __name__ == "__main__":
    show(sys.argv[1], "--all" in sys.argv)
