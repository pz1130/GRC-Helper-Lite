"""NIST SP 800-53 Rev.5 官方 OSCAL JSON → 框架导入模板。

用法：python seeds/convert_800_53.py <OSCAL catalog> <baseline JSON 目录> seeds/nist-800-53-r5.csv

官方 catalog：
https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json

层级：Family(1) → Control(2) → Enhancement(3)。
**AC-2 本身即一条真控制**，而 AC-2(1) 是它的子节点，所以「叶子即要求」
会错误地把 AC-2 排除。故凡是控制项（非 Family）一律显式标 is_requirement=true。
"""

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

COLUMNS = ["code", "title", "description", "parent_code", "attributes_json"]
ENHANCEMENT = re.compile(r"^([A-Z]{2}-\d+)(?:\(\d+\)|\.\d+)$")
PARAMETER = re.compile(r"\{\{\s*insert:\s*param,\s*([^}]+?)\s*\}\}")


def canonical_code(code: str) -> str:
    match = re.fullmatch(r"([A-Z]{2})-(\d+)(?:[.(](\d+)[)]?)?", code.strip().upper())
    if not match:
        return code.strip().upper()
    family, control, enhancement = match.groups()
    result = f"{family}-{int(control)}"
    return f"{result}.{int(enhancement)}" if enhancement else result


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _flatten_controls(groups: list[dict[str, Any]]):
    for group in groups:
        family = canonical_code(str(group.get("id", "")))
        for entry in group.get("controls") or []:
            yield family, entry
            yield from _flatten_nested(entry.get("controls") or [], family)


def _flatten_nested(entries: list[dict[str, Any]], family: str):
    for entry in entries:
        yield family, entry
        yield from _flatten_nested(entry.get("controls") or [], family)


def _statement_parts(part: dict[str, Any]) -> list[str]:
    pieces = [_clean(part.get("prose"))] if part.get("prose") else []
    for child in part.get("parts") or []:
        pieces.extend(_statement_parts(child))
    return pieces


def _render_statement(entry: dict[str, Any]) -> str:
    statement = next(
        (part for part in entry.get("parts") or [] if part.get("name") == "statement"),
        None,
    )
    return " ".join(_statement_parts(statement)) if statement else ""


def _parameter_values(entry: dict[str, Any]) -> dict[str, str]:
    values = {}
    for param in entry.get("params") or []:
        label = _clean(param.get("label"))
        if not label and param.get("select", {}).get("choice"):
            choices = [_clean(choice) for choice in param["select"]["choice"]]
            label = "one or more of " + ", ".join(choices)
        values[param.get("id", "")] = label or "organization-defined value"
    return values


def _render_entry_description(entry: dict[str, Any]) -> str:
    description = _render_statement(entry)
    values = _parameter_values(entry)
    for _ in range(4):
        rendered = PARAMETER.sub(
            lambda match: values.get(match.group(1).strip(), "organization-defined value"),
            description,
        )
        if rendered == description:
            break
        description = rendered
    return description


def _profile_ids(raw: dict[str, Any]) -> set[str]:
    catalog = raw.get("catalog", raw)
    found: set[str] = set()

    def visit(entries: list[dict[str, Any]]) -> None:
        for entry in entries:
            if entry.get("class") in {"SP800-53", "SP800-53-enhancement"}:
                found.add(canonical_code(entry["id"]))
            visit(entry.get("controls") or [])

    visit(catalog.get("groups") or [])
    return found


def _membership_from_source(source: Path) -> dict[str, list[str]]:
    raw = json.loads(source.read_text(encoding="utf-8")) if source.is_file() else None
    if isinstance(raw, dict) and raw and all(isinstance(value, list) for value in raw.values()):
        return {canonical_code(key): sorted(value) for key, value in raw.items()}

    paths = sorted(source.glob("*baseline-resolved-profile_catalog*.json")) if source.is_dir() else [source]
    membership: dict[str, set[str]] = {}
    for path in paths:
        level = re.search(r"(?:^|_)(LOW|MODERATE|HIGH)(?:_|-)", path.name.upper())
        if not level:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for code in _profile_ids(payload):
            membership.setdefault(code, set()).add(level.group(1).lower())
    return {code: sorted(levels) for code, levels in membership.items()}


def parent_of(code: str) -> str:
    match = ENHANCEMENT.match(code)
    if match:
        return match.group(1)
    if "-" in code:
        return code.split("-", 1)[0]
    return ""


def convert(controls: Path, baselines: Path, target: Path) -> int:
    raw = json.loads(controls.read_text(encoding="utf-8"))
    catalog = raw.get("catalog", raw)
    entries = list(_flatten_controls(catalog.get("groups") or []))
    membership = _membership_from_source(baselines)

    families = {
        canonical_code(group["id"]): _clean(group.get("title")) or canonical_code(group["id"])
        for group in catalog.get("groups") or []
    }
    rows = []
    for family, entry in entries:
        status = next(
            (prop.get("value") for prop in entry.get("props") or [] if prop.get("name") == "status"),
            "active",
        )
        if status == "withdrawn":
            continue
        code = canonical_code(entry["id"])
        rows.append([
            code,
            _clean(entry.get("title")) or code,
            _render_entry_description(entry),
            parent_of(code),
            json.dumps(
                {
                    "baselines": sorted(membership.get(code, [])),
                    "is_requirement": True,
                },
                ensure_ascii=False,
            ),
        ])

    family_rows = [
        [family, name, "", "", ""] for family, name in sorted(families.items())
    ]
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows(family_rows + rows)
    return len(family_rows) + len(rows)


if __name__ == "__main__":
    count = convert(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    print(f"wrote {count} rows to {sys.argv[3]}")
