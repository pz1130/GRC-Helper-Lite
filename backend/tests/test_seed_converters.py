"""官方 JSON 转换器的结构过滤与文本清洗回归测试。"""

import csv
import importlib.util
import json
from pathlib import Path

SEEDS = Path(__file__).resolve().parents[1] / "seeds"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, SEEDS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_csf_converter_filters_withdrawn_and_implementation_entries(tmp_path):
    converter = _module("convert_csf")
    source = tmp_path / "csf.json"
    target = tmp_path / "csf.csv"
    source.write_text(json.dumps({
        "response": {"elements": [{
            "elementIdentifier": "GV",
            "elementTypeIdentifier": "function",
            "title": "GOVERN",
            "text": "Govern text.",
            "elements": [{
                "elementIdentifier": "GV.OC",
                "elementTypeIdentifier": "category",
                "title": "Organizational Context",
                "text": "Context text.",
                "elements": [
                    {
                        "elementIdentifier": "GV.OC-01",
                        "elementTypeIdentifier": "subcategory",
                        "title": "",
                        "text": "The mission is understood.",
                        "elements": [],
                    },
                    {
                        "elementIdentifier": "ID.BE-01",
                        "elementTypeIdentifier": "subcategory",
                        "title": "",
                        "text": "Withdrawn text.",
                        "elements": [{"elementTypeIdentifier": "withdraw_reason"}],
                    },
                    {
                        "elementIdentifier": "GV.OC-01.001",
                        "elementTypeIdentifier": "implementation_example",
                        "title": "Example",
                        "text": "Not a core row.",
                    },
                ],
            }],
        }]}} , ensure_ascii=False), encoding="utf-8")

    assert converter.convert(source, target) == 3
    rows = list(csv.DictReader(target.open(encoding="utf-8")))
    assert [row["code"] for row in rows] == ["GV", "GV.OC", "GV.OC-01"]
    assert rows[-1]["title"] == "The mission is understood."


def test_800_53_converter_reads_oscal_and_resolved_baseline_directory(tmp_path):
    converter = _module("convert_800_53")
    controls = tmp_path / "controls.json"
    baselines = tmp_path / "baselines"
    baselines.mkdir()
    target = tmp_path / "800-53.csv"
    entry = {
        "id": "ac-1",
        "class": "SP800-53",
        "title": "Policy and Procedures",
        "params": [{"id": "ac-1_prm_1", "label": "organization-defined roles"}],
        "parts": [{
            "name": "statement",
            "parts": [{"name": "item", "prose": "Develop, document, and disseminate to {{ insert: param, ac-1_prm_1 }}."}],
        }],
    }
    enhancement = {
        "id": "ac-2(1)",
        "class": "SP800-53-enhancement",
        "title": "Automated Management",
        "parts": [{"name": "statement", "prose": "Manage accounts automatically."}],
    }
    controls.write_text(json.dumps({"catalog": {"groups": [{
        "id": "ac", "title": "Access Control", "controls": [entry, {**entry, "id": "ac-2", "title": "Accounts"}, enhancement]
    }]}}, ensure_ascii=False), encoding="utf-8")
    for level, ids in (("LOW", ["ac-1"]), ("MODERATE", ["ac-2"]), ("HIGH", ["ac-2.1"])):
        (baselines / f"NIST_{level}-baseline-resolved-profile_catalog-min.json").write_text(
            json.dumps({"catalog": {"groups": [{"controls": [
                {"id": code, "class": "SP800-53" if "." not in code else "SP800-53-enhancement"}
                for code in ids
            ]}]}}, ensure_ascii=False), encoding="utf-8"
        )

    assert converter.convert(controls, baselines, target) == 4
    rows = list(csv.DictReader(target.open(encoding="utf-8")))
    by_code = {row["code"]: row for row in rows}
    assert by_code["AC-1"]["description"] == "Develop, document, and disseminate to organization-defined roles."
    assert "{{ insert:" not in by_code["AC-1"]["description"]
    assert json.loads(by_code["AC-1"]["attributes_json"])["baselines"] == ["low"]
    assert json.loads(by_code["AC-2"]["attributes_json"])["baselines"] == ["moderate"]
    assert json.loads(by_code["AC-2.1"]["attributes_json"])["baselines"] == ["high"]
