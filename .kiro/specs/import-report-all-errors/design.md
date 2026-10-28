# Import Report All Errors Bugfix Design

## Overview

When a spreadsheet import fails validation, the import endpoints surface only the first problem, forcing the user to fix-and-reupload once per error. The framework importer (`backend/app/frameworks/importer.py`, `import_framework`) builds a full `report` from `parse_rows` but raises only `report[0]`. The Excel control-matrix importer (`backend/app/matrix/importer.py`) does the same at three validation points — it calls `validate(...)` to build a `report`, then raises `report[0].rejected()`, discarding the rest.

The fix changes only the aggregation (first → all) at those import validation points. Each importer raises a single **envelope error** — `framework.import_invalid` for the framework side, `matrix.import_invalid` for the matrix side — whose `params` carry the full issue list. Every issue entry keeps its own `code`, `detail`, and `params` (row number already lives inside `params`), serialized with the **same `asdict(issue)` shape the `/validate` preview already emits**, so the frontend can render the import envelope with the exact component it already uses for the validate report.

The fix must obey the project i18n iron rule: the API contract carries machine-readable `code` literals plus optional `params`, never concatenated human-readable sentences. "Report all errors" means a **structured list** wrapped in an envelope `code`, not a joined string. The two new envelope codes are added to `en.json` and `zh.json` so `make i18n-check` and `make verify` pass.

Transactional "一行都不写" behavior, the `/validate` preview endpoints, genuinely single-condition errors (`framework.key_exists`, `matrix.file_mismatch`, etc.), and single-issue reports are all preserved unchanged.

## Glossary

- **Bug_Condition (C)**: a non-empty import validation `report` (one or more issues). The old code raises only `report[0]`, dropping the rest when the report has more than one issue.
- **Property (P)**: a failed import raises a single envelope error whose `params.issues` list contains **every** issue from `report`, each preserving its own `code`/`params`, and writes nothing.
- **Preservation**: inputs that are not multi-issue reports — a successful import (empty report), a single-issue report, and genuinely single-condition errors (e.g. `framework.key_exists`, `matrix.file_mismatch`) — behave exactly as before. `/validate` is unchanged.
- **report**: the list of validation `Issue` objects produced by `parse_rows` (framework) or `validate(...)` (matrix). `Issue` is a frozen dataclass `{code: str, detail: str, params: dict}` (`backend/app/matrix/template.py`). Row number, when applicable, lives inside `params` (e.g. `{"row": 3}`).
- **envelope error**: one `AppError`/`SheetRejected` carrying `code="framework.import_invalid"` or `code="matrix.import_invalid"` and `params={"issues": [...], "count": N}`.
- **asdict(issue)**: `dataclasses.asdict` on an `Issue`, producing `{"code", "detail", "params"}`. Both `/validate` endpoints already emit `[asdict(issue) for issue in report]`; the envelope reuses this exact shape.
- **import_framework**: the framework import entry point in `backend/app/frameworks/importer.py`; raises `FrameworkImportError` (an `AppError` subclass) on validation failure.
- **validate_mapping_proposal / import_rows / import_confirmed**: the three matrix validation points in `backend/app/matrix/importer.py` that call `validate(...)` and currently raise `report[0].rejected()`.

## Bug Details

### Bug Condition

The bug manifests when an import validation step produces a `report` with **more than one** issue. The importer collapses the report to its first element: the framework path raises `FrameworkImportError(first.detail, code=first.code, params=first.params)`; the matrix path raises `report[0].rejected()`. Either way, issues `report[1:]` are discarded and never reach the user.

**Formal Specification:**
```
FUNCTION isBugCondition(report)
  INPUT: report of type List<Issue>
  OUTPUT: boolean

  // 导入校验产生了报告，而且报告里不止一条问题——
  // 旧代码只抛 report[0]，其余被丢弃。
  RETURN length(report) > 1
END FUNCTION
```

### Examples

- A framework file missing the `code` column AND with a duplicate code on row 5: `parse_rows` returns two issues (`framework.missing_column`, then—on a different file that passes column checks—`framework.row_code_duplicate`). Expected: both reported. Actual (unfixed): only the first.
- A framework file where rows 3, 7, and 12 each have an empty `title`: `report` holds three `framework.row_code_title_required` issues with `params={"row": 3}`, `{"row": 7}`, `{"row": 12}`. Expected: all three row numbers surfaced. Actual (unfixed): only row 3.
- A matrix sheet with an unmapped required field AND a column mapped to a non-existent header: `validate(...)` returns `matrix.required_field_unmapped` and `matrix.column_missing`. Expected: both. Actual (unfixed): only the first via `report[0].rejected()`.
- Edge case (single issue): a matrix sheet whose only problem is duplicate codes → `report` has exactly one issue. Expected behavior after fix: still fully surfaced (wrapped in the envelope with `count=1`); no information lost.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Transactional no-write on failure: when validation fails, the import SHALL continue to write nothing (framework rolls back / raises before any `session.add`; matrix raises before `import_rows` adds proposals).
- `framework.key_exists` (duplicate key) continues to raise its single `code`/`params` unchanged — it is a genuinely single-condition error, not a validation report.
- The non-report single-condition matrix errors continue unchanged: `matrix.not_mapping_proposal`, `matrix.proposal_payload_invalid`, `matrix.proposal_source_invalid`, `matrix.source_binding_changed`, `matrix.proposal_unknown_fields`, `matrix.proposal_not_found`, `matrix.proposal_not_confirmed`, `matrix.mapping_mismatch`, `matrix.file_mismatch`.
- The `/validate` preview endpoints (framework `POST /api/frameworks/validate`, matrix `POST /api/matrix/validate`) continue to return `{"ok", "report": [asdict(issue) ...], ...}` in their existing shape.
- A single-issue report still surfaces that issue's `code`/`params` to the user — wrapped in the envelope as a one-element `issues` list.

**Scope:**
All inputs that do NOT produce a multi-issue import validation report should be unaffected by this fix. This includes:
- Successful imports (empty report) — framework is written, matrix proposals are created.
- Reads/parse-level rejections raised before validation (`SheetRejected` from `read_sheet`, file-too-large), which already carry their own single `code` and are handled by the router's `_rejected`.
- Any call to the `/validate` preview endpoints.

**Note:** The expected correct behavior for the bug condition itself is defined in the Correctness Properties section (Property 1). This section focuses on what must NOT change.

## Hypothesized Root Cause

This is not a latent defect to be diagnosed — the root cause is explicit and intentional in the code, documented by a comment at each site:

> 报告可能有好几条，错误只能带一个 code：给第一条。全部问题看「校验」。

1. **Deliberate first-only aggregation**: Both importers assume an error can carry only one `code`, so they pick `report[0]` and point the user at the `/validate` preview for the full list. The envelope pattern (a `code` whose `params` carry a list of per-issue `{code, params}`) removes that assumption — `params` already flows through `jsonable_encoder` and can hold a list of dicts.

2. **Framework site** (`import_framework`): `first = report[0]; raise FrameworkImportError(first.detail, code=first.code, params=first.params)`. One site.

3. **Matrix sites** (`matrix/importer.py`): three identical `if report: raise report[0].rejected()` blocks in `validate_mapping_proposal`, `import_rows`, and `import_confirmed`. The fix should share one helper across all three so they cannot drift.

4. **Serialization already proven**: `/validate` already returns `[asdict(issue) for issue in report]` through FastAPI's JSON encoder; the `AppError` handler serializes `params` via `jsonable_encoder`. A `params` holding `{"issues": [asdict(issue), ...], "count": N}` serializes cleanly by the same machinery.

## Correctness Properties

Property 1: Bug Condition - Import Surfaces All Validation Issues

_For any_ import whose validation `report` is non-empty (isBugCondition true when it holds more than one issue; the property also covers the one-issue boundary), the fixed importer SHALL raise exactly one envelope error whose `code` is `framework.import_invalid` (framework) or `matrix.import_invalid` (matrix), whose `params.count` equals `length(report)`, and whose `params.issues` is the list `[asdict(issue) for issue in report]` in report order — so `issues[i].code == report[i].code` and `issues[i].params == report[i].params` for every `i` (row numbers preserved inside `params`) — while writing nothing to the database.

**Validates: Requirements 2.1, 2.2, 2.3, 3.1, 3.5**

Property 2: Preservation - Non-Multi-Issue Inputs Unchanged

_For any_ input that is not a multi-issue import validation report — a successful import (empty report), a single-condition error (`framework.key_exists`, `matrix.file_mismatch`, `matrix.mapping_mismatch`, `matrix.proposal_not_found`, and the other non-report matrix rejections), or a `/validate` preview call — the fixed code SHALL produce exactly the same result as the original code: same written rows (or none), same single `code`/`params`, same `/validate` response shape.

**Validates: Requirements 3.2, 3.3, 3.4, 3.6**

## Fix Implementation

### Changes Required

Assuming our root-cause analysis is correct (it is explicit in the code), the change is a targeted aggregation swap plus the shared helper, envelope i18n entries, and frontend rendering.

#### 1. Envelope issue serialization (shared helper)

**File**: `backend/app/matrix/template.py`

**Function**: new module-level helper `rejected_report(report: list[Issue]) -> SheetRejected`

```
FUNCTION rejected_report(report)
  INPUT: report, a non-empty List<Issue>
  OUTPUT: SheetRejected

  issues := [asdict(issue) FOR issue IN report]      # {code, detail, params}
  RETURN SheetRejected(
    "导入校验未通过，共 N 个问题",                      # 中文兜底摘要，N = len(report)
    code="matrix.import_invalid",                      # 字面量 code，i18n-check 扫得到
    params={"issues": issues, "count": length(report)},
  )
END FUNCTION
```

- Place it next to `Issue`/`SheetRejected` so all three matrix sites import one thing. `asdict` is `dataclasses.asdict`, already imported in the routers for `/validate`; import it in `template.py` for the helper.
- The envelope message is a short Chinese summary (fallback only); the real rendering is per-issue via codes on the frontend.
- The `code="matrix.import_invalid"` is a string literal so `scripts/check_i18n_codes.py` registers it.

#### 2. Matrix importer — three sites share the helper

**File**: `backend/app/matrix/importer.py`

**Functions**: `validate_mapping_proposal`, `import_rows`, `import_confirmed`

Replace each of the three `if report: raise report[0].rejected()` blocks with:

```
if report:
    raise rejected_report(report)
```

Import `rejected_report` from `app.matrix.template`. Update the stale comment "给第一条。全部问题看「校验」" to reflect that all issues are now carried. No other logic changes — the raise still happens before any `session.add`, preserving the no-write invariant. `import_confirmed`'s `except (ValueError, AppError)` in the router still catches `SheetRejected` (a `ValueError`) and rolls back, unchanged.

#### 3. Framework importer — envelope raise

**File**: `backend/app/frameworks/importer.py`

**Function**: `import_framework`

Replace:
```
first = report[0]
raise FrameworkImportError(first.detail, code=first.code, params=first.params)
```
with:
```
raise FrameworkImportError(
    f"框架导入校验未通过，共 {len(report)} 个问题",
    code="framework.import_invalid",
    params={"issues": [asdict(issue) for issue in report], "count": len(report)},
)
```

- Import `asdict` from `dataclasses` in `importer.py`.
- `code="framework.import_invalid"` is a literal (i18n-check requirement).
- `framework.key_exists` below is untouched — it is a single-condition error, not a report.
- The raise occurs before any `session.add`/`flush`, so no rows are written. The router's `run_import` does not wrap `import_framework` in a rollback on `AppError` (only `_read`'s `ValueError` path rolls back), but since nothing was added before the raise, there is nothing to roll back and the request fails cleanly — identical to the unfixed single-issue behavior.

#### 4. i18n entries (both locales)

**Files**: `frontend/src/locales/en.json`, `frontend/src/locales/zh.json`

Add, under the existing `errors.framework` and `errors.matrix` objects (nested keys — no dotted keys, per i18n-check's `dotted_keys` rule):

- `errors.framework.import_invalid` — en: `"The framework import failed validation ({{count}} issues). Fix the problems below and re-upload."`; zh: `"框架导入校验未通过（共 {{count}} 个问题）。请修正以下问题后重新上传。"`
- `errors.matrix.import_invalid` — en: `"The Excel import failed validation ({{count}} issues). Fix the problems below and re-upload."`; zh: `"Excel 导入校验未通过（共 {{count}} 个问题）。请修正以下问题后重新上传。"`

The `{{count}}` slot must appear identically in both locales (i18n-check's `check_locale_parity` compares placeholders). These envelope messages are the summary header the UI shows above the per-issue list. Each per-issue `code` (e.g. `framework.row_code_duplicate`, `matrix.column_missing`) already exists in both locales — unchanged.

#### 5. Frontend — render the envelope's issue list

**Files**: `frontend/src/pages/Frameworks.tsx` and `frontend/src/pages/Controls.tsx` (the `MatrixImport` component), reusing existing `issueText`/`Issue` from `frontend/src/i18n.ts`.

Today the import mutations do `onError: (caught) => setError(caught.message)` — a single string. The change: in each import `onError`, detect an envelope error and render its `params.issues` with the **same `<ul>` + `issueText(issue)` pattern already used for the `/validate` report** in the same files.

- `ApiError` already exposes `code` and `params` (see `api.ts`). When `error.code === "framework.import_invalid"` (resp. `"matrix.import_invalid"`), read `error.params.issues` as `Issue[]` and store it in a small piece of state (e.g. `importIssues`), then render `<ul>{importIssues.map((issue, i) => <li key={i}>{issueText(issue)}</li>)}</ul>` in the existing error region.
- The summary line uses the envelope message (already rendered by `ApiError`'s constructor via `errors.framework.import_invalid` / `errors.matrix.import_invalid`, with `{{count}}`).
- For any other error code, keep the current single-message behavior. Keep it minimal: the issue list uses the `Issue` interface and `issueText` helper unchanged; no new rendering primitives.
- The `issues` entries from the backend are `{code, detail, params}` — exactly the `Issue` shape `issueText` expects, matching `/validate`.

### Audit XLSX import — explicitly out of scope

Per the requirements, PRIMARY scope is framework + matrix. The audit XLSX importer (`backend/app/audit/xlsx.py`, `parse_questions`) is **deliberately left unchanged**, for a concrete reason: it does **not** have the `report[0]`-discard defect. It raises sequential `SheetRejected` per issue-class (`audit.xlsx_questions_blank`, `audit.xlsx_questions_too_long`, `audit.xlsx_languages_invalid`) and **already batches the row numbers** inside each issue's `params` (e.g. `params={"rows": blank[:20]}`). So a single audit upload already reports every blank row, every too-long row, and every bad-language row in one pass — it only serializes across the three *classes* of problem. Converting it to the envelope shape would be a larger, behavior-changing refactor (it has no `validate`/report builder to wrap) for no clear user benefit, and would widen the blast radius of a fix meant to be surgical. Recommendation: leave audit as-is; revisit separately if cross-class batching is ever requested.

## Testing Strategy

### Validation Approach

Two phases. First, reproduce the bug on unfixed code: feed each importer a file with more than one validation issue and observe that it raises only `report[0]` (missing the rest). Then verify the fix raises the envelope with every issue, and verify the preservation cases (empty report, single-issue report, single-condition errors, `/validate`) are untouched.

Tests run via `make test` in Docker (`docker compose run --rm ... api pytest -v`). Test names and comments in Chinese; commit message in English. The i18n entries (step 4 of Fix Implementation) MUST be added, or `make verify`'s `i18n-check` will fail because the backend now emits two `code=` literals (`framework.import_invalid`, `matrix.import_invalid`) with no matching catalogue entries.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix, and confirm the root cause (first-only aggregation). If a test unexpectedly shows all issues on unfixed code, re-hypothesize.

**Test Plan**: Construct in-memory inputs that make `parse_rows` / `validate` return a multi-issue report, call the importer against a test session, and assert on the raised error. Run on UNFIXED code first to observe the first-only behavior.

**Test Cases**:
1. **框架多问题导入** (framework): headers valid, rows with two distinct problems (e.g. a duplicate code on one row and an empty title on another) → `report` has ≥2 issues. Unfixed `import_framework` raises `FrameworkImportError` carrying only `report[0]`'s code (will fail the "all issues present" assertion).
2. **矩阵多问题导入** (matrix, `import_rows`): a `validate(...)` report with ≥2 issues (e.g. `matrix.required_field_blank` + `matrix.field_too_long`). Unfixed raises `report[0].rejected()` only.
3. **矩阵确认路径多问题** (matrix, `import_confirmed` / `validate_mapping_proposal`): drive a report with ≥2 issues through the confirmed path; unfixed surfaces only the first.
4. **边界：恰好一条问题** (edge): a report with exactly one issue — confirms the fix still wraps it (`count=1`, one-element `issues`) without losing information.

**Expected Counterexamples**:
- On unfixed code: the raised error's `code` equals a per-issue code (`framework.row_*` / `matrix.*`) and there is no `params.issues` list, so only one of several problems is visible.
- Root cause confirmed: first-only aggregation at the three matrix sites and the one framework site.

### Fix Checking

**Goal**: For every input where the report is non-empty, the fixed importer raises the envelope and carries all issues, writing nothing.

**Pseudocode:**
```
FOR ALL report WHERE isBugCondition(report) DO
  error := importer_fixed(input_producing(report))
  ASSERT error.code = envelope_code              # framework.import_invalid / matrix.import_invalid
  ASSERT error.params["count"] = length(report)
  ASSERT length(error.params["issues"]) = length(report)
  FOR EACH i IN 0..length(report)-1 DO
    ASSERT error.params["issues"][i]["code"]   = report[i].code
    ASSERT error.params["issues"][i]["params"] = report[i].params   # 行号在 params 里
  END FOR
  ASSERT wrote_nothing()                         # 事务性：一行都不写
END FOR
```

### Preservation Checking

**Goal**: For every input where the report is not a multi-issue report, the fixed importer behaves exactly as the original.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(report_of(input)) DO
  ASSERT importer_original(input) = importer_fixed(input)
END FOR

# 覆盖：report 为空（导入成功，框架落库 / 矩阵建提案）、
# report 恰好一条（信封里 count=1，信息不丢）、
# 非报告类单条错误（framework.key_exists、matrix.file_mismatch、
# matrix.mapping_mismatch、matrix.proposal_not_found …）、
# 以及 /validate 预览响应形状不变。
```

**Testing Approach**: Property-based testing is recommended for preservation checking because it generates many report/non-report inputs across the domain, catches edge cases manual tests miss, and gives strong confidence that non-buggy inputs are unchanged. Observe behavior on UNFIXED code first (success path writes rows; single-condition errors raise their one code), then capture that behavior in properties.

**Test Cases**:
1. **空报告导入成功** (framework): valid file → framework row written, `item_count` correct; identical before and after fix.
2. **空报告导入成功** (matrix): valid sheet through `import_confirmed` → proposals created; identical before and after.
3. **framework.key_exists 不变**: importing a duplicate key still raises the single `framework.key_exists` with its `{"key": ...}` params — not the envelope.
4. **矩阵单条件错误不变**: `matrix.file_mismatch`, `matrix.mapping_mismatch`, `matrix.proposal_not_found`, `matrix.source_binding_changed` each still raise their single code unchanged.
5. **恰好一条问题仍完整**: single-issue report → envelope with `count=1` and a one-element `issues` list whose single entry equals `asdict(report[0])`.
6. **/validate 形状不变**: both `/validate` endpoints still return `{"ok", "report": [{code, detail, params}, ...], ...}`.

### Unit Tests

- Framework: `import_framework` with a multi-issue file asserts the envelope `code`, `count`, and ordered `issues` (codes + params incl. row numbers); asserts no `Framework`/`FrameworkItem` rows written.
- Matrix: each of the three sites (`validate_mapping_proposal`, `import_rows`, `import_confirmed`) with a multi-issue report asserts `matrix.import_invalid` and the full `issues` list; asserts no `Proposal` rows written.
- Shared helper `rejected_report`: given a `list[Issue]`, returns a `SheetRejected` with `code="matrix.import_invalid"` and `params={"issues": [...], "count": N}` where each entry matches `asdict`.
- Serialization: the `AppError` handler / `jsonable_encoder` turns the envelope into `{code, message, params: {issues:[...], count}}` cleanly (nested list of dicts).

### Property-Based Tests

- Generate random multi-issue reports and assert the envelope's `issues` list equals `[asdict(i) for i in report]` in order (Fix Checking property).
- Generate random non-report inputs (valid files, single-condition triggers) and assert fixed output equals original output (Preservation property).
- Generate reports of size exactly 1 and assert the one-element envelope preserves the issue.

### Integration Tests

- `POST /api/frameworks/import` with a multi-issue file returns a 400 body `{code: "framework.import_invalid", message, params: {issues, count}}`; database unchanged.
- `POST /api/matrix/import` with a multi-issue sheet returns `{code: "matrix.import_invalid", ...}`; no proposals created.
- `POST /api/frameworks/validate` and `POST /api/matrix/validate` still return their existing `{ok, report, ...}` shape (regression guard for 3.4).
- Frontend: the import error handler, given an envelope error, renders every issue via `issueText` (the same `<ul>` used for the validate report); a non-envelope error still renders a single message. `make verify` (which includes `i18n-check`) passes with the two new catalogue entries present in both locales.
