# Bugfix Requirements Document

## Introduction

When a spreadsheet import fails validation, the import endpoints report only the first problem found, not the full list. The framework importer (`backend/app/frameworks/importer.py`, `import_framework`) collects a full `report` from `parse_rows` but raises only `report[0]` as a `FrameworkImportError`. The Excel control-matrix importer (`backend/app/matrix/importer.py`) does the same at three validation points — it calls `validate(...)` to build a `report`, then raises `report[0].rejected()`.

The consequence: a user with a file that has several problems must re-upload once per error to discover them one at a time. A preview path already exists that returns everything — the framework `POST /validate` endpoint returns `{"ok": not report, "report": [asdict(issue) ...], "items": ...}`, and the matrix side has an analogous validate path (code comment: "全部问题看「校验」"). The product intent is that Validate shows all problems; this bug is that Import historically surfaced only the first. K9 (from `KNOWN_ISSUES.md`) asks that import failures also carry all the problems so a user can fix everything in one pass.

This fix changes only the aggregation (first → all) at the import validation points. It must obey the project's i18n iron rule: the API contract carries machine-readable `code` literals (plus optional `params`), never concatenated human-readable sentences. "Report all errors" therefore means returning a **structured list** where each issue keeps its own `code`/`params` (and row number where applicable), wrapped in an envelope error `code` whose `params` carry the list. The `AppError` → JSON response shape is `{code, message, params?}`, and the handler serializes `params` via `jsonable_encoder`, so `params` can hold a list of per-issue `{code, params, ...}` objects. Each per-issue `code` already exists in `en.json` (the same codes the single-error path used and that `/validate` already emits); the new envelope code(s) must be added to `en.json`, which `make i18n-check` enforces.

## Bug Analysis

### Current Behavior (Defect)

When an import fails validation with multiple problems, only the first problem is surfaced to the caller.

1.1 WHEN a framework import (`import_framework`) runs `parse_rows` and the returned `report` contains more than one issue THEN the system raises `FrameworkImportError` carrying only `report[0]`'s `code`/`params`, discarding the remaining issues

1.2 WHEN an Excel control-matrix validation point (`validate_mapping_proposal`, `import_rows`, or `import_confirmed` in `matrix/importer.py`) produces a `report` with more than one issue THEN the system raises `report[0].rejected()` (a `SheetRejected` with only the first issue's `code`/`params`), discarding the remaining issues

1.3 WHEN an import fails with multiple problems THEN the user must fix the one reported problem and re-upload to discover the next, repeating once per problem

### Expected Behavior (Correct)

A failed framework import and a failed matrix import return all validation issues at once, each issue preserving its own `code`/`params` and row number where the issue has one, matching what the Validate/preview endpoint already shows.

2.1 WHEN a framework import runs `parse_rows` and the returned `report` is non-empty THEN the system SHALL raise a single envelope error (e.g. `code="framework.import_invalid"`) whose `params` carry the full list of issues, each entry preserving its own `code`, `params`, and row number where present

2.2 WHEN an Excel control-matrix validation point produces a non-empty `report` THEN the system SHALL raise a single envelope error (e.g. `code="matrix.import_invalid"`) whose `params` carry the full list of issues, each entry preserving its own `code`, `params`, and row number where present

2.3 WHEN the import failure response is serialized THEN the system SHALL keep every per-issue `code` identical to the code the single-error path and the `/validate` preview already emit, and SHALL add the new envelope `code`(s) to `en.json` so `make i18n-check` passes

2.4 WHEN the frontend receives an import-failure envelope error THEN it SHALL render the full list of issues rather than a single message

### Unchanged Behavior (Regression Prevention)

3.1 WHEN an import fails validation THEN the system SHALL CONTINUE TO write nothing (transactional "一行都不写" behavior is preserved)

3.2 WHEN a framework import encounters a duplicate key THEN the system SHALL CONTINUE TO raise the single-condition error `framework.key_exists` with its existing `code`/`params`, unchanged

3.3 WHEN any import encounters a genuinely single-condition error that is not a validation report (e.g. `matrix.source_binding_changed`, `matrix.proposal_not_found`, `matrix.mapping_mismatch`, `matrix.file_mismatch`) THEN the system SHALL CONTINUE TO raise that single `code` with its existing behavior

3.4 WHEN the `/validate` preview endpoints (framework and matrix) are called THEN they SHALL CONTINUE TO return the full report in their existing response shape, unchanged

3.5 WHEN an import `report` contains exactly one issue THEN the system SHALL still surface that issue's `code`/`params` to the user (whether wrapped in the envelope or raised directly is a design decision; the single issue's information must not be lost)

3.6 WHEN `make i18n-check` and `make verify` run THEN they SHALL CONTINUE TO pass

## Bug Condition and Properties

**Key definitions:**
- **F**: the original import function (framework or matrix) before the fix
- **F'**: the fixed import function
- **report**: the list of validation issue objects produced by `parse_rows` (framework) or `validate(...)` (matrix); each issue has `.code`, `.detail`/`.params`, and a row number where applicable

### Bug Condition

```pascal
FUNCTION isBugCondition(report)
  INPUT: report of type List<Issue>
  OUTPUT: boolean

  // 导入校验产生了报告，而且报告里不止一条问题——
  // 旧代码只抛 report[0]，其余被丢弃。
  RETURN length(report) > 1
END FUNCTION
```

### Fix Checking

```pascal
// Property: Fix Checking —— 导入失败要带上全部问题
FOR ALL report WHERE isBugCondition(report) DO
  error ← F'(report)                      // 捕获被抛出的信封错误
  ASSERT error.code = envelope_code       // framework.import_invalid / matrix.import_invalid
  ASSERT length(issues_of(error)) = length(report)
  FOR EACH i IN 0..length(report)-1 DO
    ASSERT issues_of(error)[i].code   = report[i].code
    ASSERT issues_of(error)[i].params = report[i].params   // 行号在 params 里一并保留
  END FOR
  ASSERT wrote_nothing(F')                // 事务性：一行都不写
END FOR
```

### Preservation Checking

```pascal
// Property: Preservation Checking —— 非 bug 输入行为不变
FOR ALL input WHERE NOT isBugCondition(report_of(input)) DO
  ASSERT F(input) = F'(input)
END FOR

// 覆盖：report 为空（导入成功）、report 恰好一条、
// 以及非报告类单条错误（framework.key_exists、matrix.file_mismatch 等）。
```
