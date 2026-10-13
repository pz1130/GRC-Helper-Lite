import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/**
 * Excel 控制矩阵导入原来整条链路都在产中文句子：校验报告是一串中文，
 * 拒绝只给 `matrix.request_rejected` 加一句中文 detail——英文界面上，
 * 用户核对列映射时看到的全是中文。现在后端只给 code 与参数。
 */

const FILE = { name: "matrix.xlsx", mimeType: "application/octet-stream", buffer: Buffer.from("x") };

async function mockMatrix(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      return route.fulfill({ json: { id: 1, email: "lead@example.com", name: "Lead", role: "grc_lead" } });
    }
    if (url.pathname === "/api/matrix/propose-mapping") {
      return route.fulfill({ json: {
        id: 41, kind: "matrix_mapping", status: "pending", citations: [], confidence: 0.9, document_id: null,
        payload: {
          mapping: { title: "名称", statement: "描述" },
          source_headers: ["编号", "名称", "描述"], source_rows: 3, source_sha256: "a".repeat(64),
        },
      } });
    }
    if (url.pathname === "/api/matrix/validate") {
      return route.fulfill({ json: {
        ok: false, rows: 3,
        report: [
          { code: "matrix.required_field_blank", detail: "有 2 行的 title 为空", params: { count: 2, field: "title" } },
          { code: "matrix.duplicate_codes", detail: "编号重复：AC-1、AC-2", params: { codes: ["AC-1", "AC-2"] } },
        ],
      } });
    }
    return route.fulfill({ json: [] });
  });
}

test("英文界面上，列映射的校验报告显示成英文", async ({ page }) => {
  await mockMatrix(page);
  await page.goto("/controls");
  await page.getByText("Import an Excel control matrix").click();
  await page.getByLabel("Excel file").setInputFiles(FILE);
  await page.getByRole("button", { name: "Propose column mapping" }).click();
  await page.getByRole("button", { name: "Validate all rows" }).click();

  const report = page.getByRole("alert");
  await expect(report).toContainText("title is empty in 2 rows");
  await expect(report).toContainText("Duplicate codes: AC-1");
  expect(await report.innerText(), "英文界面上漏出了后端的中文").not.toMatch(/为空|编号重复/);
});

test("英文界面上，Excel 被拒绝的原因显示成英文", async ({ page }) => {
  await mockMatrix(page);
  await page.route("**/api/matrix/propose-mapping", (route) => route.fulfill({
    status: 400, json: { code: "matrix.formula_cells", message: "Excel 包含公式或错误单元格，请先粘贴为值" },
  }));
  await page.goto("/controls");
  await page.getByText("Import an Excel control matrix").click();
  await page.getByLabel("Excel file").setInputFiles(FILE);
  await page.getByRole("button", { name: "Propose column mapping" }).click();

  await expect(page.getByRole("alert")).toContainText("paste them as values first");
});

// 框架模板的校验报告是同一类问题：原来是一串中文句子。
test("英文界面上，框架模板的校验报告显示成英文", async ({ page }) => {
  await mockMatrix(page);
  await page.route("**/api/frameworks/validate", (route) => route.fulfill({ json: {
    ok: false, items: 1,
    report: [{
      code: "framework.parent_missing",
      detail: "「PR.AA-01」的 parent_code 指向不存在的编号「MISSING」",
      params: { code: "PR.AA-01", parent: "MISSING" },
    }],
  } }));
  await page.goto("/frameworks");
  await page.getByLabel("Framework file").setInputFiles(FILE);
  await page.getByRole("button", { name: "Validate", exact: true }).click();

  const report = page.getByRole("alert");
  await expect(report).toContainText('"PR.AA-01" has a parent_code "MISSING" that does not exist');
  expect(await report.innerText(), "英文界面上漏出了后端的中文").not.toMatch(/指向不存在/);
});
