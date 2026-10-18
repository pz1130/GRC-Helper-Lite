"""K16 人工抽看表：从映射评估的逐对明细里抽样，生成一份 Excel 供人判断。

    python -m evals.review_sheet results/mapping-<label>-pairs.json out.xlsx

要回答的问题：召回 0.23 是模型真漏了，还是 OLIR 收了牵强关联（K17）？
三张抽样表各问一件事——
· 模型见过没选：候选里有、模型没给。判成「真关联」多，就该动提示词。
· 预筛没给：模型根本没看到。判成「真关联」多，预筛还有空间。
· 模型多给：OLIR 没有、模型给了。判成「真关联」多，精确率 0.52 也偏悲观。
只用公开的 NIST 文本；固定种子，同一份明细抽出同一批。
"""

import json
import random
import sys
from pathlib import Path
from typing import Any

from evals import corpus

SEED = 17
VERDICTS = {
    "seen": ["真关联，模型该给", "牵强，OLIR 偏宽", "不相关"],
    "unseen": ["真关联，模型该给", "牵强，OLIR 偏宽", "不相关"],
    "extra": ["真关联，OLIR 漏收", "牵强", "错"],
}
TITLES = {"seen": "模型见过没选", "unseen": "预筛没给", "extra": "模型多给"}


def sample(
    pairs: dict[str, list[dict[str, Any]]], *, n_seen: int = 30, n_unseen: int = 15, n_extra: int = 25,
) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(SEED)
    pools = {
        "seen": [p for p in pairs["missed"] if p["in_candidates"]],
        "unseen": [p for p in pairs["missed"] if not p["in_candidates"]],
        "extra": pairs["extra"],
    }
    sizes = {"seen": n_seen, "unseen": n_unseen, "extra": n_extra}
    out = {}
    for kind, pool in pools.items():
        picked = rng.sample(pool, min(sizes[kind], len(pool)))
        out[kind] = sorted(picked, key=lambda p: (p["csf"], p["control"]))
    return out


def _texts() -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    rows = {row["code"]: row for row in corpus._rows("nist-csf-2.0.csv")}

    def item(code: str) -> str:
        row = rows.get(code, {})
        parent = rows.get(row.get("parent_code", ""), {})
        head = f"[{parent.get('title', '')}] " if parent else ""
        return head + (row.get("description") or row.get("title") or "")

    controls = {c["code"]: (c["title"], c["statement"]) for c in corpus.mapping_controls()}
    return {code: item(code) for code in rows}, controls


def build(pairs: dict[str, list[dict[str, Any]]], out: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    picked = sample(pairs)
    items, controls = _texts()
    font, bold = Font(name="Arial", size=10), Font(name="Arial", size=10, bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    todo = PatternFill("solid", start_color="FFF2CC")

    wb = Workbook()
    summary = wb.active
    summary.title = "汇总"

    for kind in ("seen", "unseen", "extra"):
        ws = wb.create_sheet(TITLES[kind])
        head = ["#", "CSF 子类", "CSF 原文", "800-53", "控制点标题", "模型看到的陈述（截 250 字）"]
        if kind == "extra":
            head += ["模型给的强度", "模型理由"]
        head += ["判断", "备注"]
        ws.append(head)
        for n, p in enumerate(picked[kind], 1):
            title, statement = controls.get(p["control"], ("", ""))
            row = [n, p["csf"], items.get(p["csf"], ""), p["control"], title, statement]
            if kind == "extra":
                row += [p.get("strength", ""), p.get("rationale", "")]
            ws.append([*row, "", ""])
        verdict_col = len(head) - 1
        letter = get_column_letter(verdict_col)
        rule = DataValidation(type="list", formula1='"' + ",".join(VERDICTS[kind]) + '"', allow_blank=True)
        ws.add_data_validation(rule)
        if picked[kind]:
            rule.add(f"{letter}2:{letter}{len(picked[kind]) + 1}")
        widths = [4, 11, 50, 9, 24, 50] + ([10, 50] if kind == "extra" else []) + [18, 24]
        for col, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = width
        for cells in ws.iter_rows():
            for cell in cells:
                cell.font, cell.alignment = (bold if cell.row == 1 else font), wrap
        for r in range(2, len(picked[kind]) + 2):
            ws.cell(row=r, column=verdict_col).fill = todo
        ws.freeze_panes = "C2"

    seen_all = sum(1 for p in pairs["missed"] if p["in_candidates"])
    population = {"seen": seen_all, "unseen": len(pairs["missed"]) - seen_all, "extra": len(pairs["extra"])}
    summary.append(["类别", "总体对数", "抽样", "已判", *VERDICTS["seen"], "说明"])
    notes = {
        "seen": "候选里有、模型没给。前两列多 → 动提示词有意义；第二列多 → 是评测口径问题",
        "unseen": "预筛没给模型看。第一列多 → 预筛还有空间",
        "extra": "OLIR 没有、模型给了（三列依次为：OLIR 漏收 / 牵强 / 错）。第一列多 → 精确率也偏悲观",
    }
    for i, kind in enumerate(("seen", "unseen", "extra"), 2):
        sheet = f"'{TITLES[kind]}'"
        # 「判断」列：前 6 列固定，「模型多给」多两列强度与理由
        letter = get_column_letter(7 + (2 if kind == "extra" else 0))
        rng = f"{sheet}!{letter}2:{letter}{len(picked[kind]) + 1}"
        counts = [f'=COUNTIF({rng},"{v}")' for v in VERDICTS[kind]]
        summary.append([TITLES[kind], population[kind], len(picked[kind]), f"=COUNTA({rng})", *counts, notes[kind]])
    for col, width in zip("ABCDEFGH", [14, 10, 8, 8, 18, 16, 10, 70], strict=True):
        summary.column_dimensions[col].width = width
    for cells in summary.iter_rows():
        for cell in cells:
            cell.font, cell.alignment = (bold if cell.row == 1 else font), wrap
    summary.append([])
    source = ("来源：backend/evals/results 里的映射评估逐对明细（NIST CSF 2.0 × SP 800-53 Rev.5，"
              f"OLIR 对照）；随机种子 {SEED}。")
    summary.append([source])
    summary.cell(row=summary.max_row, column=1).font = font

    wb.save(out)
    return out


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    print(build(json.loads(src.read_text(encoding="utf-8")), out))


if __name__ == "__main__":
    main()
