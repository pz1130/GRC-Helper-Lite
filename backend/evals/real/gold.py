"""由大模型标注 + 人工复核修正生成标准答案，并计算与规则标注、解析器输出的分歧。

    python -m evals.real.gold            # 写 gold/<id>.gold.json 与 work/disagreement.json

人工修正记录在 review/<id>.json（提交进仓库，可审计）：
  {"reviewed": true, "note": "...", "drop": [行号...], "add": [{line, level, number, heading}],
   "set": {"行号": {字段: 值}}, "relevel": {"from_level": to_level}}
"""
from __future__ import annotations

import json
import re

from evals.real.extract import HERE, lines, parse_path
from evals.real.label import WORK, manifest

GOLD = HERE / "gold"
REVIEW = HERE / "review"


def nz(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower().rstrip("。.:：")


def apply_review(doc_id: str, labels: list[dict]) -> tuple[list[dict], dict]:
    f = REVIEW / f"{doc_id}.json"
    rv = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    drop = set(rv.get("drop", []))
    out = [dict(x) for x in labels if x["line"] not in drop]
    for k, v in rv.get("set", {}).items():
        for x in out:
            if x["line"] == int(k):
                x.update(v)
    out += rv.get("add", [])
    rl = {int(a): b for a, b in rv.get("relevel", {}).items()}
    for x in out:
        x["level"] = rl.get(x["level"], x["level"])
    return sorted(out, key=lambda x: x["line"]), rv


_SENT = re.compile(r"(?<=[。！？；])|(?<=[.!?;])\s+(?=[A-Z“\"(（])")


def _sentences(body: str, prefixes: list[str]) -> list[str]:
    sents = [s.strip() for s in _SENT.split(body) if s and s.strip()]
    if sents:  # 首句去掉与标题重合的前缀（编号、标题本身）
        first = sents[0]
        for p in prefixes:
            if p and nz(first).startswith(nz(p)):
                # 在原文里按非空白字符数切掉前缀
                need, i = len(nz(p)), 0
                while need and i < len(first):
                    if not first[i].isspace():
                        need -= 1
                    i += 1
                first = first[i:].lstrip(" ：:.-—")
        sents[0] = first
    good = [s for s in sents if 10 <= len(nz(s)) <= 400]
    pick = good[:1] + good[-1:] if len(good) > 1 else good
    return pick


_DOTTED = re.compile(r"^(\d{1,2}(?:\.\d{1,2})+)\.?\s+(\S.*)$")


def numbered_paragraphs(row: dict, ls: list[str], labels: list[dict]) -> list[dict]:
    """英文约定：父号已是条款的多级编号段落（2.1 / 4.3.2）本身也是可引用的条款单位。

    大模型按「标题」标注，常把「4.3.2 Passwords must not…」这种编号段落当正文漏掉；
    审计引用恰恰引用这个号。确定性补齐：父号必须已在标注里（挡掉表格、日期、版本号），
    目录行（引导点）不收。读起来是一句话的，标题就用编号本身。
    """
    if row["lang"] != "en":
        return labels
    out = [dict(x) for x in labels]
    have = {x["number"].rstrip("."): x for x in out if x.get("number")}
    taken = {x["line"] for x in out}
    found = []
    for i, line in enumerate(ls):
        m = _DOTTED.match(line.strip())
        if m and not re.search(r"\.{4,}|…{2,}", line):
            found.append((m[1].count("."), i, m[1], m[2]))
    for _, i, number, rest in sorted(found):
        parent = have.get(number.rsplit(".", 1)[0])
        if parent is None or number in have or i in taken:
            continue
        sentence = len(rest) > 80 or rest.rstrip().endswith((".", ";", ":"))
        lab = {"line": i, "level": parent["level"] + 1, "number": number,
               "heading": number if sentence else rest.strip(), "auto": "numbered_paragraph"}
        out.append(lab)
        have[number] = lab
        taken.add(i)
    return sorted(out, key=lambda x: x["line"])


def build(row: dict) -> tuple[dict, dict]:
    ls = lines(row)
    labels = json.loads((WORK / "labels" / f"{row['id']}.llm.json").read_text(encoding="utf-8"))
    labels, rv = apply_review(row["id"], numbered_paragraphs(row, ls, labels))
    nodes, stack = [], []
    for i, lab in enumerate(labels):
        end = labels[i + 1]["line"] if i + 1 < len(labels) else len(ls)
        body = " ".join(ls[lab["line"]:end])
        heading = lab["heading"] or lab["number"]
        node = {"number": lab["number"] or None, "heading": heading,
                "sentences": _sentences(body, [lab["number"], heading if heading != lab["number"] else ""]),
                "table_cells": [], "children": [], "line": lab["line"]}
        while stack and stack[-1][0] >= lab["level"]:
            stack.pop()
        (stack[-1][1]["children"] if stack else nodes).append(node)
        stack.append((lab["level"], node))
    return {"id": row["id"], "lang": row["lang"], "clauses": nodes,
            "reviewed": bool(rv.get("reviewed")), "review_note": rv.get("note", "")}, rv


def _f1(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    tp = len(a & b)
    return round(2 * tp / (len(a) + len(b)), 3) if tp else 0.0


def disagreement(row: dict, gold_labels: list[dict]) -> dict:
    rule = json.loads((WORK / "labels" / f"{row['id']}.rule.json").read_text(encoding="utf-8"))
    g_lines = {x["line"] for x in gold_labels}
    r_lines = {x["line"] for x in rule}
    # 规则与大模型：按行号比对（±1 行容差）
    r_fuzzy = {next((g for g in (r, r - 1, r + 1) if g in g_lines), r) for r in r_lines}
    from app.parsing.registry import get_parser
    try:
        parsed = get_parser(parse_path(row)).parse(parse_path(row))
        keys = set()

        def walk(ns):
            for n in ns:
                if n.kind == "section":
                    keys.add(nz(n.heading).removeprefix(nz(n.number)) or nz(n.number))
                walk(n.children)
        walk(parsed.clauses)
    except Exception:  # noqa: BLE001
        keys = set()
    g_keys = {nz(x["heading"] or x["number"]).removeprefix(nz(x["number"])) or nz(x["number"]) for x in gold_labels}
    return {"rule_f1": _f1(g_lines, r_fuzzy), "parser_f1": _f1(g_keys, keys),
            "gold": len(gold_labels), "rule": len(rule), "parser": len(keys)}


def main() -> None:
    GOLD.mkdir(exist_ok=True)
    report = {}
    for row in manifest():
        if not (WORK / "labels" / f"{row['id']}.llm.json").exists():
            continue
        gold, _ = build(row)
        (GOLD / f"{row['id']}.gold.json").write_text(json.dumps(gold, ensure_ascii=False, indent=1), encoding="utf-8")
        raw = json.loads((WORK / "labels" / f"{row['id']}.llm.json").read_text(encoding="utf-8"))
        labels, _ = apply_review(row["id"], numbered_paragraphs(row, lines(row), raw))
        report[row["id"]] = disagreement(row, labels) | {"reviewed": gold["reviewed"]}
    (WORK / "disagreement.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    for k, v in report.items():
        print(f"{k:22s} {v}")


if __name__ == "__main__":
    main()
