"""审计准备包的 Excel 成员。"""

import io
from typing import Any

from openpyxl import Workbook

# 表头原来写死中文，不管导出语言是什么——英文界面导出的准备包里，这两张表是中文的。
MAPPING_HEADERS = [
    "Control code",
    "Control title",
    "Framework item code",
    "Framework item title",
    "Strength",
    "Confidence",
    "Rationale",
]
EVIDENCE_HEADERS = ["Control code", "Evidence title", "Type", "Owner", "Collected on", "Freshness"]


def _save(headers: list[str], rows: list[list[Any]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _evidence_values(row: Any) -> list[Any]:
    if isinstance(row, (list, tuple)):
        return list(row)
    collected = row.last_collected_at
    owner_id = row.owner_user_id
    return [
        row.control_code or "",
        row.title,
        row.evidence_type_name or "",
        "" if owner_id is None else str(owner_id),
        collected.date().isoformat() if collected else "",
        row.display_status,
    ]


def build_mappings_xlsx(rows) -> bytes:
    return _save(MAPPING_HEADERS, [list(row) for row in rows])


def build_evidence_xlsx(rows) -> bytes:
    return _save(EVIDENCE_HEADERS, [_evidence_values(row) for row in rows])
