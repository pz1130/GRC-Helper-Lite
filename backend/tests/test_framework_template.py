from app.frameworks.template import parse_rows

HEADERS = ["code", "title", "description", "parent_code", "attributes_json"]


def rows(*values):
    return [list(value) for value in values]


def test_a_valid_forest_parses_with_levels_derived_from_parents():
    parsed, report = parse_rows(HEADERS, rows(
        ["PR", "Protect", "", "", ""],
        ["PR.AA", "Identity", "", "PR", ""],
        ["PR.AA-01", "Identities managed", "Text.", "PR.AA", ""],
        ["DE", "Detect", "", "", ""],
    ))
    assert report == []
    assert [(r.code, r.level) for r in parsed] == [
        ("PR", 1), ("PR.AA", 2), ("PR.AA-01", 3), ("DE", 1)]


def test_multiple_roots_are_legal():
    _, report = parse_rows(HEADERS, rows(["A", "a", "", "", ""], ["B", "b", "", "", ""]))
    assert report == []


def _codes(report):
    # 报告是 code + 参数，前端按语言组句；中文 detail 只是兜底，不作断言。
    return [(issue.code, issue.params) for issue in report]


def test_duplicate_codes_are_reported():
    _, report = parse_rows(HEADERS, rows(["A", "a", "", "", ""], ["A", "b", "", "", ""]))
    assert _codes(report) == [("framework.row_code_duplicate", {"row": 2, "code": "A"})]


def test_parent_code_pointing_nowhere_is_reported():
    _, report = parse_rows(HEADERS, rows(["A", "a", "", "ZZZ", ""]))
    assert _codes(report) == [("framework.parent_missing", {"code": "A", "parent": "ZZZ"})]


def test_a_parent_cycle_is_reported():
    _, report = parse_rows(HEADERS, rows(["A", "a", "", "B", ""], ["B", "b", "", "A", ""]))
    assert {code for code, _ in _codes(report)} == {"framework.parent_cycle"}


def test_a_self_parent_is_reported():
    _, report = parse_rows(HEADERS, rows(["A", "a", "", "A", ""]))
    assert _codes(report) == [("framework.parent_cycle", {"code": "A"})]


def test_missing_required_column_is_reported():
    _, report = parse_rows(["code", "description"], rows(["A", ""]))
    assert _codes(report) == [("framework.missing_column", {"column": "title"})]


def test_blank_required_cell_is_reported():
    _, report = parse_rows(HEADERS, rows(["", "a", "", "", ""]))
    assert _codes(report) == [("framework.row_code_title_required", {"row": 1})]


def test_attributes_json_is_parsed_and_bad_json_is_reported():
    parsed, report = parse_rows(HEADERS, rows(
        ["A", "a", "", "", '{"baselines": ["moderate"], "is_requirement": true}']))
    assert report == []
    assert parsed[0].attributes == {"baselines": ["moderate"], "is_requirement": True}

    _, bad = parse_rows(HEADERS, rows(["A", "a", "", "", "{not json"]))
    assert _codes(bad) == [("framework.row_attributes_not_json", {"row": 1})]


def test_attributes_json_must_be_an_object():
    _, report = parse_rows(HEADERS, rows(["A", "a", "", "", "[1,2]"]))
    assert _codes(report) == [("framework.row_attributes_not_object", {"row": 1})]


def test_depth_beyond_the_limit_is_reported():
    from app.frameworks.template import MAX_DEPTH

    chain = [["N0", "n0", "", "", ""]]
    for i in range(1, MAX_DEPTH + 1):
        chain.append([f"N{i}", f"n{i}", "", f"N{i - 1}", ""])
    _, report = parse_rows(HEADERS, chain)
    assert _codes(report) == [("framework.too_deep", {"code": f"N{MAX_DEPTH}", "limit": MAX_DEPTH})]


def test_empty_sheet_is_reported():
    _, report = parse_rows(HEADERS, [])
    assert _codes(report) == [("framework.no_rows", {})]
