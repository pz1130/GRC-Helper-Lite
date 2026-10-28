# Implementation Plan

- [x] 1. Write bug condition exploration test (import surfaces all validation issues)
  - **Property 1: Bug Condition** - Import Surfaces All Validation Issues
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists (importers raise only `report[0]`)
  - **DO NOT attempt to fix the test or the code when it fails** - the failure is the expected, correct outcome at this step
  - **NOTE**: This test encodes the expected behavior (the envelope error carrying every issue) - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate issues `report[1:]` are discarded
  - **Scoped PBT Approach**: For the deterministic framework/matrix paths, scope the property to concrete multi-issue reports so counterexamples are reproducible; optionally generate random multi-issue reports (`isBugCondition(report)` true when `length(report) > 1`) and assert the envelope carries `[asdict(issue) for issue in report]` in order
  - Build in-memory inputs that make `parse_rows` / `validate(...)` return a report with ≥2 issues (Bug Condition in design: `isBugCondition(report) = length(report) > 1`):
    - **框架多问题导入**: call `import_framework` against a test session with a file carrying two distinct problems (e.g. a duplicate code on one row and an empty title on another) → `report` has ≥2 issues
    - **矩阵多问题导入 (import_rows)**: drive a `validate(...)` report with ≥2 issues (e.g. `matrix.required_field_blank` + `matrix.field_too_long`)
    - **矩阵确认路径多问题 (import_confirmed / validate_mapping_proposal)**: drive a ≥2-issue report through the confirmed path
  - Assert the raised error is the envelope: `error.code` is `framework.import_invalid` (framework) / `matrix.import_invalid` (matrix); `error.params["count"] == len(report)`; `error.params["issues"] == [asdict(issue) for issue in report]` so `issues[i]["code"] == report[i].code` and `issues[i]["params"] == report[i].params` (row numbers preserved inside `params`) for every `i`; and assert no rows were written (事务性：一行都不写)
  - Run tests on UNFIXED code via `make test` (Docker: `docker compose run --rm ... api pytest -v`)
  - **EXPECTED OUTCOME**: Tests FAIL on unfixed code - the raised error's `code` is a per-issue code (`framework.row_*` / `matrix.*`) with no `params.issues` list, so only `report[0]` is visible (this is correct - it proves the bug exists)
  - Document counterexamples found (e.g. "framework import with duplicate-code + empty-title raises only the first code; second issue lost")
  - Chinese test names and comments; commit message in English
  - Mark task complete when tests are written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 2.1, 2.2, 2.3, 3.1_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Non-Multi-Issue Inputs Unchanged
  - **IMPORTANT**: Follow observation-first methodology - run the UNFIXED code for each non-bug input, record the actual output, then write tests asserting that observed output
  - Property-based testing recommended: generate non-report inputs (valid files, single-condition triggers) across the domain and assert fixed output equals observed original output; stronger guarantee that `NOT isBugCondition(report)` cases are untouched
  - Observe and capture behavior on UNFIXED code for inputs where `NOT isBugCondition(report)` (Preservation Requirements in design):
    - **空报告导入成功 (framework)**: valid file → `import_framework` writes the `Framework` row with correct `item_count`
    - **空报告导入成功 (matrix)**: valid sheet through `import_confirmed` → proposals created
    - **framework.key_exists 不变**: importing a duplicate key raises the single `framework.key_exists` with its `{"key": ...}` params (not the envelope)
    - **矩阵单条件错误不变**: `matrix.file_mismatch`, `matrix.mapping_mismatch`, `matrix.proposal_not_found`, `matrix.source_binding_changed` each raise their single `code` unchanged
    - **/validate 形状不变**: framework `POST /api/frameworks/validate` and matrix `POST /api/matrix/validate` return `{"ok", "report": [{code, detail, params}, ...], ...}` in the existing shape
    - **失败时一行都不写**: a failed import writes nothing (transactional invariant)
  - Write property-based tests capturing the observed behavior patterns
  - Run tests on UNFIXED code via `make test`
  - **EXPECTED OUTCOME**: Tests PASS on unfixed code (this confirms the baseline behavior to preserve)
  - Chinese test names and comments; commit message in English
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 3. Fix: import surfaces all validation issues (envelope aggregation)

  - [x] 3.1 Implement the backend fix (shared helper + matrix sites + framework site + i18n)
    - Add shared helper to `backend/app/matrix/template.py`: `rejected_report(report: list[Issue]) -> SheetRejected` returning `SheetRejected("导入校验未通过，共 N 个问题", code="matrix.import_invalid", params={"issues": [asdict(issue) for issue in report], "count": len(report)})`; place it next to `Issue`/`SheetRejected`; import `asdict` from `dataclasses`; keep `code` a string literal so `scripts/check_i18n_codes.py` registers it
    - In `backend/app/matrix/importer.py`, replace each of the three `if report: raise report[0].rejected()` blocks in `validate_mapping_proposal`, `import_rows`, and `import_confirmed` with `if report: raise rejected_report(report)`; import `rejected_report` from `app.matrix.template`; update the stale comment ("给第一条。全部问题看「校验」") to reflect that all issues are now carried; keep the raise before any `session.add` (no-write invariant)
    - In `backend/app/frameworks/importer.py`, replace `first = report[0]; raise FrameworkImportError(first.detail, code=first.code, params=first.params)` with `raise FrameworkImportError(f"框架导入校验未通过，共 {len(report)} 个问题", code="framework.import_invalid", params={"issues": [asdict(issue) for issue in report], "count": len(report)})`; import `asdict` from `dataclasses`; leave `framework.key_exists` untouched (single-condition error)
    - Add nested i18n entries to `frontend/src/locales/en.json` and `frontend/src/locales/zh.json` (no dotted keys — nest under existing `errors.framework` / `errors.matrix`):
      - `errors.framework.import_invalid` — en: `"The framework import failed validation ({{count}} issues). Fix the problems below and re-upload."`; zh: `"框架导入校验未通过（共 {{count}} 个问题）。请修正以下问题后重新上传。"`
      - `errors.matrix.import_invalid` — en: `"The Excel import failed validation ({{count}} issues). Fix the problems below and re-upload."`; zh: `"Excel 导入校验未通过（共 {{count}} 个问题）。请修正以下问题后重新上传。"`
      - `{{count}}` must appear identically in both locales (parity); REQUIRED or `make verify`'s `i18n-check` fails because the backend now emits two new `code=` literals with no catalogue entry
    - Audit xlsx import (`backend/app/audit/xlsx.py`) is explicitly OUT of scope — leave unchanged
    - _Bug_Condition: isBugCondition(report) = length(report) > 1 (from design)_
    - _Expected_Behavior: raise one envelope error whose params.issues = [asdict(issue) for issue in report], params.count = len(report), writing nothing (Property 1 from design)_
    - _Preservation: empty report, single-condition errors, /validate shape, and no-write-on-failure unchanged (Property 2 from design)_
    - _Requirements: 2.1, 2.2, 2.3, 3.1_

  - [x] 3.2 Implement the frontend envelope rendering
    - In the framework import `onError` handler (`frontend/src/pages/Frameworks.tsx`) and the matrix import `onError` handler (the `MatrixImport` component in `frontend/src/pages/Controls.tsx`): when `error.code === "framework.import_invalid"` (resp. `"matrix.import_invalid"`), read `error.params.issues` as `Issue[]` into small state (e.g. `importIssues`) and render `<ul>{importIssues.map((issue, i) => <li key={i}>{issueText(issue)}</li>)}</ul>` using the existing `issueText`/`Issue` from `frontend/src/i18n.ts` — the same `<ul>` + `issueText()` pattern already used for the `/validate` report in these files
    - The summary header uses the envelope message rendered by `ApiError`'s constructor (via the new `errors.*.import_invalid` catalogue entries with `{{count}}`)
    - For any other error code, keep the current single-message behavior (`setError(caught.message)`); add no new rendering primitives
    - _Bug_Condition: isBugCondition(report) = length(report) > 1 (from design)_
    - _Expected_Behavior: frontend renders the full issue list from the envelope's params.issues rather than a single message (Requirement 2.4)_
    - _Preservation: non-envelope errors still render a single message (Property 2 from design)_
    - _Requirements: 2.4_

  - [x] 3.3 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Import Surfaces All Validation Issues
    - **IMPORTANT**: Re-run the SAME tests from task 1 - do NOT write new tests
    - The tests from task 1 encode the expected behavior; when they pass, they confirm the envelope carries every issue (`code`, `count`, ordered `issues` with codes/params and row numbers) and nothing is written
    - Run the bug condition exploration tests from task 1 via `make test`
    - **EXPECTED OUTCOME**: Tests PASS (confirms the bug is fixed — Fix Checking)
    - _Requirements: 2.1, 2.2, 2.3, 3.1_

  - [x] 3.4 Verify preservation tests still pass
    - **Property 2: Preservation** - Non-Multi-Issue Inputs Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run the preservation property tests from task 2 via `make test`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions — empty report, single-condition errors, /validate shape, single-issue report, no-write-on-failure all unchanged)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 4. Supplementary unit and integration tests
  - **Unit tests** (backend, via `make test`):
    - `rejected_report`: given a `list[Issue]`, returns a `SheetRejected` with `code="matrix.import_invalid"` and `params={"issues": [...], "count": N}` where each entry equals `asdict(issue)`
    - Serialization: the `AppError` handler / `jsonable_encoder` turns the envelope into `{code, message, params: {issues: [...], count}}` cleanly (nested list of dicts)
    - **恰好一条问题 (count=1 boundary)**: a single-issue report → envelope with `count=1` and a one-element `issues` list whose entry equals `asdict(report[0])` (information not lost)
  - **Integration tests** (backend, via `make test`):
    - `POST /api/frameworks/import` with a multi-issue file returns a 400 body `{code: "framework.import_invalid", message, params: {issues, count}}`; database unchanged (no `Framework`/`FrameworkItem` rows)
    - `POST /api/matrix/import` with a multi-issue sheet returns `{code: "matrix.import_invalid", message, params: {issues, count}}`; no `Proposal` rows created
    - Regression: `POST /api/frameworks/validate` and `POST /api/matrix/validate` still return `{ok, report: [{code, detail, params}, ...], ...}` (guards 3.4)
  - **i18n/frontend** (via `make verify`): confirm `make verify`'s `i18n-check` passes with the two new catalogue entries present and placeholder-matched in both locales, and the frontend build succeeds
  - Chinese test names and comments; commit message in English
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.4, 3.5, 3.6_

- [x] 5. Checkpoint - Ensure all tests pass
  - Run `make test` (backend pytest in Docker) and `make verify` (includes `i18n-check` and the frontend build — both are touched by this change) and ensure everything passes
  - Confirm bug condition tests pass (Fix Checking), preservation tests pass (no regressions), and i18n parity holds
  - Ensure all tests pass; ask the user if questions arise
  - _Requirements: 3.6_
