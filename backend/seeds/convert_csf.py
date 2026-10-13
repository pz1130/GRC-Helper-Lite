"""NIST CSF 2.0 官方 JSON → 框架导入模板。

用法：python seeds/convert_csf.py <官方 JSON> seeds/nist-csf-2.0.csv

官方 Reference Tool JSON：
https://csrc.nist.gov/extensions/nudp/services/json/csf/elements

CSF 层级：Function(1) → Category(2) → Subcategory(3)。
只有 Subcategory 是真要求，交由「叶子即要求」的默认规则处理，
因此本脚本不写 is_requirement。
"""

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

COLUMNS = ["code", "title", "description", "parent_code", "attributes_json"]
def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _first_sentence(text: str) -> str:
    text = _clean(text)
    match = re.search(r"(?<=[.!?])\s+", text)
    return text[:match.start()] if match else text


def _official_elements(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        elements = raw.get("response", {}).get("elements")
        if isinstance(elements, list):
            return elements
    raise ValueError("CSF JSON 必须包含 response.elements")


def _official_rows(raw: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    for function in _official_elements(raw):
        if function.get("elementTypeIdentifier") != "function":
            continue
        function_code = _clean(function.get("elementIdentifier"))
        rows.append([
            function_code,
            _clean(function.get("title")) or function_code,
            _clean(function.get("text")),
            "",
            "",
        ])
        for category in function.get("elements") or []:
            if category.get("elementTypeIdentifier") != "category":
                continue
            active_subcategories = [
                subcategory
                for subcategory in category.get("elements") or []
                if subcategory.get("elementTypeIdentifier") == "subcategory"
                and "text" in subcategory
                and not any(
                    child.get("elementTypeIdentifier") == "withdraw_reason"
                    for child in subcategory.get("elements") or []
                )
            ]
            if not active_subcategories:
                continue
            category_code = _clean(category.get("elementIdentifier"))
            rows.append([
                category_code,
                _clean(category.get("title")) or category_code,
                _clean(category.get("text")),
                function_code,
                "",
            ])
            for subcategory in active_subcategories:
                code = _clean(subcategory.get("elementIdentifier"))
                text = _clean(subcategory.get("text"))
                rows.append([
                    code,
                    _clean(subcategory.get("title")) or _first_sentence(text) or code,
                    text,
                    category_code,
                    "",
                ])
    return rows


def parent_of(code: str) -> str:
    """PR.AA-01 → PR.AA；PR.AA → PR；PR → 空。"""
    if "-" in code:
        return code.rsplit("-", 1)[0]
    if "." in code:
        return code.rsplit(".", 1)[0]
    return ""


def convert(source: Path, target: Path) -> int:
    raw = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        rows = _official_rows(raw)
    else:
        rows = []
        for entry in raw:
            code = entry["element_identifier"].strip()
            rows.append([
                code,
                entry.get("title", "").strip() or code,
                entry.get("text", "").strip(),
                parent_of(code),
                "",
            ])
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    count = convert(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"wrote {count} rows to {sys.argv[2]}")
