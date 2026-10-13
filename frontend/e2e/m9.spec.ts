import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function mockMaturity(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { id: 1, email: "lead@example.com", name: "Lead", role: "grc_lead" } });
    if (url.pathname === "/api/frameworks") return route.fulfill({ json: [{ id: 7, name_zh: "网络安全框架", name_en: "Cybersecurity Framework", version: "2.0" }] });
    if (url.pathname === "/api/maturity/assessments" && request.method() === "GET") return route.fulfill({ json: [{ id: 3, framework_id: 7, name: "2026 baseline", as_of_date: "2026-09-11", status: "draft" }] });
    if (url.pathname === "/api/maturity/assessments/3/summary") return route.fulfill({ json: {
      assessment_id: 3,
      framework_id: 7,
      overall: { total_items: 4, scored_items: 3, doc_average: 3, impl_average: 2 },
      groups: [
        { framework_item_id: 10, code: "GV", title: "Govern", total_items: 2, scored_items: 1, doc_average: 4, impl_average: 3 },
        { framework_item_id: 20, code: "PR", title: "Protect", total_items: 1, scored_items: 1, doc_average: 3, impl_average: 2 },
        { framework_item_id: 30, code: "DE", title: "Detect", total_items: 1, scored_items: 1, doc_average: 2, impl_average: 1 },
      ],
      items: [
        { framework_item_id: 11, parent_id: 10, group_id: 10, code: "GV.PO-01", title: "Policy", doc_score: 4, impl_score: 3, doc_rationale: "Approved policy", impl_rationale: "Operating evidence" },
        { framework_item_id: 12, parent_id: 10, group_id: 10, code: "GV.RR-01", title: "Roles", doc_score: null, impl_score: null, doc_rationale: "", impl_rationale: "" },
        { framework_item_id: 21, parent_id: 20, group_id: 20, code: "PR.AA-01", title: "Access", doc_score: 3, impl_score: 2, doc_rationale: "Defined", impl_rationale: "Partially implemented" },
        { framework_item_id: 31, parent_id: 30, group_id: 30, code: "DE.CM-01", title: "Monitoring", doc_score: 2, impl_score: 1, doc_rationale: "Partial", impl_rationale: "Ad hoc" },
      ],
    } });
    if (url.pathname === "/api/maturity/assessments/3/scores" && request.method() === "PUT") return route.fulfill({ json: { id: 99 } });
    if (url.pathname === "/api/maturity/assessments/3/finalize") return route.fulfill({ json: { id: 3, framework_id: 7, name: "2026 baseline", as_of_date: "2026-09-11", status: "final" } });
    if (url.pathname === "/api/risks" && request.method() === "GET") return route.fulfill({ json: [] });
    if (url.pathname === "/api/risks/from-maturity-gap") return route.fulfill({ status: 201, json: { id: 8 } });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url.pathname}` } });
  });
}

test("maturity workspace shows dual rollups and saves leaf scores", async ({ page }) => {
  await mockMaturity(page);
  await page.goto("/maturity");

  await expect(page.getByRole("heading", { name: "Maturity assessment" })).toBeVisible();
  await expect(page.getByRole("img", { name: "Dual-line maturity radar chart" })).toBeVisible();
  await expect(page.getByText("3.00").first()).toBeVisible();
  await expect(page.getByText("75%")).toBeVisible();

  await page.getByLabel("GV.RR-01 Document score").selectOption("3");
  await page.getByLabel("GV.RR-01 Implementation score").selectOption("2");
  await page.getByLabel("GV.RR-01 Documentation rationale").fill("Roles are documented");
  await page.getByRole("row").filter({ hasText: "GV.RR-01" }).getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("row").filter({ hasText: "GV.RR-01" }).getByRole("status")).toHaveText("Saved.");
});

test("an empty maturity workspace does not show a permanent loading state", async ({ page }) => {
  await mockMaturity(page);
  await page.route("**/api/maturity/assessments", (route) => route.fulfill({ json: [] }));
  await page.goto("/maturity");

  await expect(page.getByText("No maturity assessments yet. Choose a framework to create the first one.")).toBeVisible();
  await expect(page.getByText("Loading…")).toHaveCount(0);
});

test("a low implementation score converts to a traceable risk", async ({ page }) => {
  await mockMaturity(page);
  await page.goto("/maturity");

  const created = page.waitForRequest((request) => request.url().endsWith("/api/risks/from-maturity-gap"));
  await page.getByRole("row").filter({ hasText: "PR.AA-01" }).getByRole("button", { name: "Create risk" }).click();
  expect((await created).postDataJSON()).toEqual({ assessment_id: 3, framework_item_id: 21 });
  await expect(page.getByRole("row").filter({ hasText: "PR.AA-01" }).getByRole("status")).toHaveText("Risk created.");
});

test("risk register plots exposure and updates mitigation", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { id: 1, email: "lead@example.com", name: "Lead", role: "grc_lead" } });
    if (url.pathname === "/api/risks/owners") return route.fulfill({ json: [{ id: 1, name: "Lead", email: "lead@example.com" }] });
    if (url.pathname === "/api/risks" && request.method() === "GET") return route.fulfill({ json: [{ id: 8, title: "PR.AA-01 implementation gap", description: "Only a pilot exists.", source: "gap", source_ref: { assessment_id: 3, framework_item_id: 21 }, likelihood: 3, impact: 4, inherent_score: 12, mitigation: "", residual_likelihood: null, residual_impact: null, residual_score: null, owner_user_id: 1, due_date: "2026-12-10", status: "open" }] });
    if (url.pathname === "/api/risks/8" && request.method() === "PATCH") return route.fulfill({ json: { id: 8 } });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url.pathname}` } });
  });
  await page.goto("/risks");

  await expect(page.getByRole("heading", { name: "Risk register" })).toBeVisible();
  await expect(page.getByLabel("Impact 4, likelihood 3, 1 risks")).toHaveText("1");
  await expect(page.getByText("PR.AA-01 implementation gap")).toBeVisible();
  await page.getByLabel("PR.AA-01 implementation gap Mitigation").fill("Complete the rollout");
  await page.getByLabel("PR.AA-01 implementation gap Residual likelihood").selectOption("2");
  await page.getByLabel("PR.AA-01 implementation gap Residual impact").selectOption("2");
  const updated = page.waitForRequest((request) => request.url().endsWith("/api/risks/8") && request.method() === "PATCH");
  await page.getByRole("row").filter({ hasText: "PR.AA-01 implementation gap" }).getByRole("button", { name: "Save" }).click();
  expect((await updated).postDataJSON()).toMatchObject({ mitigation: "Complete the rollout", residual_likelihood: 2, residual_impact: 2 });
});

// 定稿不可撤回，定稿后分数再也改不了——一次误点不能就把整份评估锁死。
test("finalizing asks first, and a failure is shown", async ({ page }) => {
  await mockMaturity(page);
  let finalizes = 0;
  await page.route("**/api/maturity/assessments/3/finalize", (route) => {
    finalizes += 1;
    return route.fulfill({ status: 409, json: { code: "maturity.assessment_already_finalized", message: "成熟度评估已经定稿" } });
  });
  await page.goto("/maturity");

  page.once("dialog", (dialog) => void dialog.dismiss());
  await page.getByRole("button", { name: "Finalize and lock" }).click();
  expect(finalizes).toBe(0);

  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "Finalize and lock" }).click();
  await expect.poll(() => finalizes).toBe(1);
  await expect(page.getByRole("alert")).toBeVisible();
});
