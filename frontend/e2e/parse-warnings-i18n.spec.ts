import { expect, test } from "@playwright/test";

const ADMIN = { email: "admin@example.com", password: "pw123456" };

const DOC = {
  id: 4242,
  title: "Collapsed Policy",
  version: null,
  owner: null,
  approver: null,
  approved_date: null,
  effective_date: null,
  doc_type: null,
  status: "active",
  parse_error: null,
  parse_warnings: "最大的一条条款装下了 72% 的正文——条款树没切开，建议人工核对",
  parse_warning_codes: [{ code: "shape.one_clause_dominates", params: { share: "72%" } }],
  ocr_quality_flag: false,
  supersedes_id: null,
  uploaded_by: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

async function signIn(page) {
  await page.route("**/api/extraction/documents/*/coverage", (route) => route.fulfill({
    json: { counts: { cited: 0, uncited: 0, not_completed: 0 }, clauses: [] },
  }));
  await page.goto("/");
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Compliance & Governance Cockpit" })).toBeVisible();
}

/**
 * `parse_warnings` 是存进库的中文句子，两处界面原样显示——英文界面上就是中文。
 * 修法与业务错误一致：并行存一份 code + 变量，前端据此翻译，中文句子留作兜底。
 */
test("英文界面上，解析告警显示成英文", async ({ page }) => {
  await signIn(page);
  await expect(page.getByRole("heading", { name: "Compliance & Governance Cockpit" })).toBeVisible();

  await page.route("**/api/documents/4242", (route) => route.fulfill({ json: DOC }));
  await page.route("**/api/documents/4242/clauses*", (route) => route.fulfill({ json: [] }));
  await page.goto("/documents/4242");

  await expect(page.getByText(/72% of the body text/)).toBeVisible();
  const text = await page.evaluate(() => document.body.innerText);
  expect(text, "英文界面上漏出了后端的中文告警").not.toContain("条款树没切开");
});

test("抽取留痕区分未引用和未完成，能打开待检查原文", async ({ page }) => {
  await signIn(page);
  const clauses = [
    { id: 501, number: "1", heading: "Review", heading_path: "Review", citation_label: "1",
      text: "Owners must review access quarterly.", level: 1, page_ref: 1, kind: "section", children: [] },
    { id: 502, number: "2", heading: "Records", heading_path: "Records", citation_label: "2",
      text: "Keep review records.", level: 1, page_ref: 2, kind: "section", children: [] },
  ];
  await page.route("**/api/documents/4242", (route) => route.fulfill({ json: DOC }));
  await page.route("**/api/documents/4242/clauses*", (route) => route.fulfill({ json: clauses }));
  await page.route("**/api/extraction/documents/4242/coverage", (route) => route.fulfill({
    json: { counts: { cited: 0, uncited: 1, not_completed: 1 }, clauses: [
      { clause_id: 501, citation_label: "1", status: "uncited" },
      { clause_id: 502, citation_label: "2", status: "not_completed" },
    ] },
  }));
  await page.goto("/documents/4242");
  const coverage = page.getByRole("region", { name: "Extraction coverage" });
  await expect(coverage.getByText("Cited: 0 · Uncited: 1 · Not completed: 1")).toBeVisible();
  await expect(coverage.getByText(/not proof that all its requirements/)).toBeVisible();
  await coverage.getByText("Inspect uncited and unprocessed clauses").click();
  await coverage.getByRole("button", { name: /1 · Uncited/ }).click();
  await expect(page.getByRole("textbox", { name: "Review", exact: true }))
    .toHaveValue("Owners must review access quarterly.");
});

/**
 * 迁移前存的行没有 code（NULL），必须原样回落到那句中文，
 * 而不是显示 `shape.one_clause_dominates` 或者干脆空掉。
 */
test("老行没有 code 时，回落到库里那句中文", async ({ page }) => {
  await signIn(page);
  const legacy = { ...DOC, id: 4243, parse_warning_codes: null };
  await page.route("**/api/documents/4243", (route) => route.fulfill({ json: legacy }));
  await page.route("**/api/documents/4243/clauses*", (route) => route.fulfill({ json: [] }));
  await page.goto("/documents/4243");

  await expect(page.getByText(/条款树没切开/)).toBeVisible();
  const text = await page.evaluate(() => document.body.innerText);
  expect(text, "原始 i18n 键漏到界面上了").not.toContain("shape.one_clause");
});

// 解析失败的原因原来只有库里那句中文，文档页与列表原样显示。产品改为只有英文界面后，
// 所有用户看到的都是中文。现在并行存 code，前端按 code 查词条。
const FAILED = {
  ...DOC, id: 4250, title: "Broken Policy", status: "parse_failed",
  parse_warnings: null, parse_warning_codes: null,
  parse_error: "无法打开 docx 文件：BadZipFile",
  parse_error_code: "parse_error.docx_unreadable",
  parse_error_params: { error: "BadZipFile" },
};

test("解析失败的原因显示成英文", async ({ page }) => {
  await signIn(page);
  await page.route("**/api/documents/4250", (route) => route.fulfill({ json: FAILED }));
  await page.route("**/api/documents/4250/clauses*", (route) => route.fulfill({ json: [] }));
  await page.goto("/documents/4250");
  await expect(page.getByText(/could not be opened \(BadZipFile\)/)).toBeVisible();

  await page.route("**/api/documents", (route) => route.request().method() === "GET"
    ? route.fulfill({ json: [FAILED] })
    : route.fallback());
  await page.goto("/documents");
  await expect(page.getByText(/could not be opened \(BadZipFile\)/)).toBeVisible();
  const text = await page.evaluate(() => document.body.innerText);
  expect(text, "界面上漏出了后端的中文失败原因").not.toContain("无法打开");
});

test("迁移前的失败行没有 code，回落到那句原文", async ({ page }) => {
  await signIn(page);
  const legacy = { ...FAILED, id: 4251, parse_error_code: null, parse_error_params: null };
  await page.route("**/api/documents/4251", (route) => route.fulfill({ json: legacy }));
  await page.route("**/api/documents/4251/clauses*", (route) => route.fulfill({ json: [] }));
  await page.goto("/documents/4251");
  await expect(page.getByText("无法打开 docx 文件：BadZipFile")).toBeVisible();
});
