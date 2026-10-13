import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const ADMIN = { email: "admin@example.com", password: "pw123456" };

// 后端那句中文是兜底，code 才是正式文案的来源。
const DUPLICATE = {
  code: "document.duplicate_upload",
  message: "该文件已存在（文档 #7：Access Policy）",
  params: { id: 7, title: "Access Policy" },
};

const DOC = {
  id: 4250, title: "Access Policy", version: null, owner: null, approver: null,
  approved_date: null, effective_date: null, review_due_date: null, doc_type: "policy",
  status: "active", parse_error: null, parse_warnings: null, parse_warning_codes: null,
  ocr_quality_flag: false, supersedes_id: null, original_filename: "a.docx",
  created_at: "2026-09-01T00:00:00Z",
};

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Compliance & Governance Cockpit" })).toBeVisible();
}

async function rejectUploads(page: Page) {
  await page.route("**/api/documents", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({ status: 409, json: DUPLICATE })
      : route.fallback(),
  );
}

const FILE = { name: "a.docx", mimeType: "application/octet-stream", buffer: Buffer.from("x") };

/**
 * 两处文档上传都自己写了 fetch，出错时抛的是后端那句中文，绕过了按 code 查词条——
 * 英文界面上传重复文件、格式不支持、超限，看到的都是中文。i18n-check 只比对
 * 后端的 code 与词条，看不见前端有没有用上它。
 */
test("英文界面上，文档列表的上传错误显示成英文", async ({ page }) => {
  await signIn(page);
  await rejectUploads(page);
  await page.goto("/documents");

  await page.getByLabel("Upload", { exact: false }).first().setInputFiles(FILE);

  await expect(page.getByText(/already exists \(document #7: Access Policy\)/)).toBeVisible();
  const text = await page.evaluate(() => document.body.innerText);
  expect(text, "英文界面上漏出了后端的中文").not.toContain("该文件已存在");
});

test("英文界面上，上传新版本的错误显示成英文", async ({ page }) => {
  await page.route("**/api/documents/4250/requirement-scan", (route) => route.fulfill({ json: {
    mode: "rules", reasons: ["disabled"], clause_count: 0, model_count: 0, rule_fallback_count: 0,
    requirements: 0, unlinked_requirements: 0, model: null, scanned_at: null, rows: [],
  } }));
  await signIn(page);
  await rejectUploads(page);
  await page.route("**/api/documents/4250", (route) => route.fulfill({ json: DOC }));
  await page.route("**/api/documents/4250/clauses*", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/extraction/documents/4250/coverage", (route) => route.fulfill({
    json: { counts: { cited: 0, uncited: 0, not_completed: 0 }, clauses: [] },
  }));
  await page.goto("/documents/4250");

  await page.getByRole("button", { name: "Import new version" }).click();
  await page.getByLabel("Upload", { exact: false }).setInputFiles(FILE);

  await expect(page.getByRole("alert")).toContainText("already exists (document #7: Access Policy)");
  const text = await page.evaluate(() => document.body.innerText);
  expect(text, "英文界面上漏出了后端的中文").not.toContain("该文件已存在");
});
