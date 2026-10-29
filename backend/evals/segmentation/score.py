"""切割准确率：解析结果对标准答案逐条打分。

两项，各 0–1，文档得分取平均，总分取全部文档平均：

- **结构 F1**：标准条款与解析出的 section 条款一一配对，标题相同且父条款也配对上
  才算对。精确率分母是解析出的全部 section 条款（多切、把列表项当条款都会扣分），
  召回分母是标准条款数（漏切、并进上一条都会扣分）。
- **内容归属率**：标准答案里每一句正文、每个表格单元格，必须出现在它所属条款
  （含该条款下的表格子节点）的文字里。挂错条款、丢字、被吞进标题都算错。

比较前去掉全部空白并转小写，PDF 折行不影响结果。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from app.parsing.contract import ClauseNode


def norm(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").lower().rstrip("。.:：")


@dataclass
class Pred:
    node: ClauseNode
    parent: Pred | None

    @property
    def full_text(self) -> str:
        parts = [self.node.heading, self.node.text]
        parts += [c.text for c in self.node.children if c.kind == "table"]
        return norm(" ".join(parts))


def _flatten_pred(nodes, parent=None, out=None):
    out = [] if out is None else out
    for n in nodes:
        if n.kind == "section":
            p = Pred(n, parent)
            out.append(p)
            _flatten_pred(n.children, p, out)
        else:
            _flatten_pred(n.children, parent, out)
    return out


def _flatten_gold(nodes, parent=None, out=None):
    out = [] if out is None else out
    for n in nodes:
        out.append((n, parent))
        _flatten_gold(n["children"], n, out)
    return out


def _heading_matches(gold: dict, pred: ClauseNode) -> bool:
    g = norm(gold["heading"])
    num = norm(gold["number"])
    h, pn = norm(pred.heading), norm(pred.number)
    candidates = {h, pn + h, h.removeprefix(pn)}
    if g in candidates or (num + g) in candidates or (g == num and h.startswith(g)):
        return True
    # 编号段落（「4.1.2 Executives must…」）没有真标题：一方用编号、一方用句子开头，
    # 或两边截断长度不同，都算同一条——编号相同且标题互为前缀/有一方只是编号。
    h = h.removeprefix(pn)
    return bool(num) and pn == num and (not h or g == num or h.startswith(g) or g.startswith(h))


def score_doc(gold: dict, clauses: list[ClauseNode]) -> dict:
    preds = _flatten_pred(clauses)
    golds = _flatten_gold(gold["clauses"])
    match: dict[int, Pred] = {}
    used: set[int] = set()
    for g, _parent in golds:
        for p in preds:
            if id(p) not in used and _heading_matches(g, p.node):
                match[id(g)] = p
                used.add(id(p))
                break
    tp = 0
    for g, parent in golds:
        p = match.get(id(g))
        if p is None:
            continue
        expected = match.get(id(parent)) if parent is not None else None
        if (parent is None and p.parent is None) or (
            parent is not None and expected is not None and p.parent is expected
        ):
            tp += 1
    precision = tp / len(preds) if preds else 0.0
    recall = tp / len(golds) if golds else 0.0
    f1 = 2 * precision * recall / (precision + recall) if tp else 0.0
    total = hit = 0
    for g, _parent in golds:
        p = match.get(id(g))
        for s in [*g["sentences"], *g["table_cells"]]:
            total += 1
            if p is not None and norm(s) in p.full_text:
                hit += 1
    content = hit / total if total else 1.0
    return {"gold": len(golds), "pred": len(preds), "correct": tp,
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "content": round(content, 3), "score": round((f1 + content) / 2, 3)}


def run(corpus: Path) -> dict:
    from app.parsing.registry import get_parser

    results = {}
    for path in sorted([*corpus.glob("*.docx"), *corpus.glob("*.pdf")]):
        gold = json.loads((corpus / f"{path.stem}.gold.json").read_text(encoding="utf-8"))
        try:
            parsed = get_parser(path).parse(path)
            results[path.name] = score_doc(gold, parsed.clauses)
        except Exception as exc:  # noqa: BLE001 — 评估要把失败记成 0 分，不中断
            results[path.name] = {"error": f"{type(exc).__name__}: {exc}", "score": 0.0,
                                  "f1": 0.0, "content": 0.0}
    scores = [r["score"] for r in results.values()]
    summary = {
        "overall": round(sum(scores) / len(scores), 3),
        "structure_f1": round(sum(r["f1"] for r in results.values()) / len(scores), 3),
        "content": round(sum(r["content"] for r in results.values()) / len(scores), 3),
    }
    for lang in ("en", "zh"):
        sub = [r["score"] for name, r in results.items() if name.startswith(lang)]
        summary[lang] = round(sum(sub) / len(sub), 3)
    return {"summary": summary, "documents": results}


if __name__ == "__main__":
    corpus = Path(sys.argv[1])
    report = run(corpus)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    for name, r in report["documents"].items():
        print(f"{name:28s} score={r['score']:.3f} f1={r['f1']:.3f} content={r['content']:.3f}"
              f" gold={r.get('gold')} pred={r.get('pred')} {r.get('error', '')}")
    print(json.dumps(report["summary"]))
