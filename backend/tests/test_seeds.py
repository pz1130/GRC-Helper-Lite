"""种子文件必须始终能通过导入校验——不写库，只解析。"""

import csv
from pathlib import Path

import pytest

from app.frameworks.template import parse_rows

SEEDS = Path(__file__).resolve().parents[1] / "seeds"


def _load(name: str) -> tuple[list[str], list[list[str]]]:
    text = (SEEDS / name).read_text(encoding="utf-8-sig")
    reader = list(csv.reader(text.splitlines()))
    return reader[0], reader[1:]


@pytest.mark.parametrize("name,minimum", [
    ("nist-csf-2.0.csv", 100),
    ("nist-800-53-r5.csv", 1000),
])
def test_seed_passes_template_validation(name, minimum):
    headers, rows = _load(name)
    parsed, report = parse_rows(headers, rows)
    assert report == [], report[:5]
    assert len(parsed) >= minimum


def test_800_53_marks_controls_as_requirements_and_carries_baselines():
    headers, rows = _load("nist-800-53-r5.csv")
    parsed, _ = parse_rows(headers, rows)
    controls = [row for row in parsed if row.attributes]
    assert controls, "800-53 控制项必须带 attributes"
    assert all(row.attributes.get("is_requirement") is True for row in controls)
    assert any("moderate" in ((row.attributes or {}).get("baselines") or []) for row in parsed)


def test_csf_leaves_requirement_flag_to_the_default_rule():
    headers, rows = _load("nist-csf-2.0.csv")
    parsed, _ = parse_rows(headers, rows)
    assert all(row.attributes is None for row in parsed)


def test_csf_seed_is_the_active_20_core_with_readable_titles():
    headers, rows = _load("nist-csf-2.0.csv")
    parsed, _ = parse_rows(headers, rows)
    subcategories = [row for row in parsed if row.level == 3]
    assert len(subcategories) == 106
    legacy_categories = {
        "ID.BE", "ID.GV", "ID.RM", "ID.SC", "PR.AC", "PR.IP", "PR.MA",
        "PR.PT", "DE.DP", "RS.RP", "RS.IM", "RC.IM",
    }
    assert all(row.parent_code not in legacy_categories for row in subcategories)
    assert all(row.title != row.code for row in subcategories)


def test_800_53_seed_uses_structured_text_without_placeholders_or_spacing_noise():
    headers, rows = _load("nist-800-53-r5.csv")
    parsed, _ = parse_rows(headers, rows)
    controls = [row for row in parsed if row.level > 1]
    assert len(controls) == 1014
    assert all("{{ insert:" not in row.description for row in controls)
    assert all("D e v e l o p" not in row.description for row in controls)
    ac1 = next(row for row in controls if row.code == "AC-1")
    assert "Develop, document, and disseminate" in ac1.description
