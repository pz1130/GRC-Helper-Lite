"""模板列定义与校验报告——纯函数，不碰数据库。"""

import json
from dataclasses import dataclass
from typing import Any

from app.matrix.template import Issue

CANONICAL_COLUMNS: tuple[str, ...] = (
    "code", "title", "description", "parent_code", "attributes_json",
)
REQUIRED_COLUMNS: tuple[str, ...] = ("code", "title")
MAX_ITEMS = 20_000
MAX_DEPTH = 8
MAX_CODE_CHARS = 64
MAX_TITLE_CHARS = 500


@dataclass
class Row:
    code: str
    title: str
    description: str = ""
    parent_code: str | None = None
    attributes: dict[str, Any] | None = None
    level: int = 1
    order_index: int = 0


def _cell(row: list[str], position: int) -> str:
    return row[position].strip() if 0 <= position < len(row) else ""


def _unique(report: list[Issue]) -> list[Issue]:
    seen: set[tuple[str, str]] = set()
    kept = []
    for issue in report:
        key = (issue.code, json.dumps(issue.params, sort_keys=True))
        if key not in seen:
            seen.add(key)
            kept.append(issue)
    return kept


def parse_rows(headers: list[str], rows: list[list[str]]) -> tuple[list[Row], list[Issue]]:
    """返回 (解析后的行, 校验报告)。报告非空即不可导入。

    报告是 code + 参数，前端按语言组句；detail 只是中文兜底。
    """
    report: list[Issue] = []
    for column in REQUIRED_COLUMNS:
        if column not in headers:
            report.append(Issue(
                code="framework.missing_column", detail=f"缺少必需列 {column}", params={"column": column},
            ))
    if report:
        return [], report
    if not rows:
        return [], [Issue(code="framework.no_rows", detail="Excel 没有数据行")]
    if len(rows) > MAX_ITEMS:
        return [], [Issue(
            code="framework.too_many_items", detail=f"条目数 {len(rows)} 超过上限 {MAX_ITEMS}",
            params={"count": len(rows), "limit": MAX_ITEMS},
        )]

    index = {column: headers.index(column) for column in CANONICAL_COLUMNS if column in headers}
    parsed: list[Row] = []
    seen: set[str] = set()
    for number, raw in enumerate(rows, start=1):
        code = _cell(raw, index["code"])
        title = _cell(raw, index["title"])
        if not code or not title:
            report.append(Issue(
                code="framework.row_code_title_required", detail=f"第 {number} 行：code 与 title 为必填",
                params={"row": number},
            ))
            continue
        if len(code) > MAX_CODE_CHARS:
            report.append(Issue(
                code="framework.row_code_too_long", detail=f"第 {number} 行：code 超过 {MAX_CODE_CHARS} 字符",
                params={"row": number, "limit": MAX_CODE_CHARS},
            ))
        if len(title) > MAX_TITLE_CHARS:
            report.append(Issue(
                code="framework.row_title_too_long", detail=f"第 {number} 行：title 超过 {MAX_TITLE_CHARS} 字符",
                params={"row": number, "limit": MAX_TITLE_CHARS},
            ))
        if code in seen:
            report.append(Issue(
                code="framework.row_code_duplicate", detail=f"第 {number} 行：code 重复「{code}」",
                params={"row": number, "code": code},
            ))
        seen.add(code)

        attributes: dict[str, Any] | None = None
        raw_attributes = _cell(raw, index["attributes_json"]) if "attributes_json" in index else ""
        if raw_attributes:
            try:
                loaded = json.loads(raw_attributes)
            except ValueError:
                report.append(Issue(
                    code="framework.row_attributes_not_json",
                    detail=f"第 {number} 行：attributes_json 不是合法 JSON", params={"row": number},
                ))
            else:
                if isinstance(loaded, dict):
                    attributes = loaded
                else:
                    report.append(Issue(
                        code="framework.row_attributes_not_object",
                        detail=f"第 {number} 行：attributes_json 必须是对象", params={"row": number},
                    ))

        parsed.append(Row(
            code=code,
            title=title,
            description=_cell(raw, index["description"]) if "description" in index else "",
            parent_code=(
                _cell(raw, index["parent_code"]) or None
            ) if "parent_code" in index else None,
            attributes=attributes,
            order_index=number - 1,
        ))

    by_code = {row.code: row for row in parsed}
    for row in parsed:
        if row.parent_code and row.parent_code not in by_code:
            report.append(Issue(
                code="framework.parent_missing",
                detail=f"「{row.code}」的 parent_code 指向不存在的编号「{row.parent_code}」",
                params={"code": row.code, "parent": row.parent_code},
            ))

    if report:
        return parsed, _unique(report)

    # 层级由父链深度推出；同时发现成环与超深。
    for row in parsed:
        depth, cursor, chain = 1, row, {row.code}
        while cursor.parent_code:
            parent = by_code[cursor.parent_code]
            if parent.code in chain:
                report.append(Issue(
                    code="framework.parent_cycle", detail=f"父子关系成环：{row.code}", params={"code": row.code},
                ))
                break
            chain.add(parent.code)
            cursor = parent
            depth += 1
            if depth > MAX_DEPTH:
                report.append(Issue(
                    code="framework.too_deep", detail=f"「{row.code}」的层级超过上限 {MAX_DEPTH}",
                    params={"code": row.code, "limit": MAX_DEPTH},
                ))
                break
        row.level = depth

    return parsed, _unique(report)
