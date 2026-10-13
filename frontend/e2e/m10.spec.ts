import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const conflictProposal = {
  id: 501,
  kind: "conflict",
  status: "pending",
  confidence: 0.82,
  payload: {
    clause_a_id: 91,
    clause_b_id: 92,
    topic: "Password rotation period",
    difference: "The policy requires 90 days; the standard allows 180 days.",
    quote_a: "every 90 days",
    quote_b: "every 180 days",
  },
  citations: [
    { clause_id: 91, quote: "every 90 days" },
    { clause_id: 92, quote: "every 180 days" },
  ],
};

export async function mockConflicts(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me")
      return route.fulfill({ json: { id: 1, email: "lead@example.com", name: "Lead", role: "grc_lead" } });
    if (url.pathname === "/api/proposals/stats")
      return route.fulfill({ json: { pending: 1, by_kind: { conflict: 1 } } });
    if (url.pathname === "/api/proposals")
      return route.fulfill({ json: [conflictProposal] });
    if (url.pathname === "/api/clauses/91")
      return route.fulfill({ json: {
        id: 91, document_id: 3, document_title: "Password Policy", number: "4.2",
        heading: "Rotation", heading_path: "Password › Rotation", citation_label: "4.2",
        text: "Passwords rotate every 90 days.", level: 2, page_ref: 4 } });
    if (url.pathname === "/api/clauses/92")
      return route.fulfill({ json: {
        id: 92, document_id: 5, document_title: "Access Standard", number: "7.1",
        heading: "Credentials", heading_path: "Access › Credentials", citation_label: "7.1",
        text: "Passwords rotate every 180 days.", level: 2, page_ref: 9 } });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url.pathname}` } });
  });
}

test("a conflict card shows both clause texts side by side", async ({ page }) => {
  await mockConflicts(page);
  await page.goto("/review");

  const card = page.locator('[data-proposal-kind="conflict"]');
  await expect(card).toBeVisible();
  await expect(card.getByText("Password rotation period")).toBeVisible();
  await expect(card.getByText("Passwords rotate every 90 days.")).toBeVisible();
  await expect(card.getByText("Passwords rotate every 180 days.")).toBeVisible();
  await expect(card.getByText("Password Policy")).toBeVisible();
  await expect(card.getByText("Access Standard")).toBeVisible();
});

test("a conflict card states what each side requires", async ({ page }) => {
  await mockConflicts(page);
  await page.goto("/review");

  await expect(
    page.getByText("The policy requires 90 days; the standard allows 180 days."),
  ).toBeVisible();
});

test("conflict cards offer no bulk accept checkbox", async ({ page }) => {
  await mockConflicts(page);
  await page.goto("/review");

  // 与 relation 提案一致：冲突要逐条看原文，不给批量确认。
  await expect(
    page.locator('[data-proposal-kind="conflict"] input[type="checkbox"]'),
  ).toHaveCount(0);
});

const impact = {
  previous_document: { id: 3, title: "Password Policy", version: "1.0" },
  parser_generation_mismatch: false,
  added: [{ clause_id: 21, citation_label: "4.4", text: "Passwords must not be reused." }],
  removed: [{ clause_id: 11, citation_label: "4.2", text: "Passwords rotate every 90 days." }],
  matched: [{
    old_clause_id: 12, new_clause_id: 22, citation_label: "4.3",
    old_text: "Passwords may be at least 8 characters.",
    text: "Passwords shall be at least 12 characters.", changed: true,
  }],
  affected_controls: [{ id: 7, code: "C-0007", title: "Password rotation" }],
  affected_mappings: [{ id: 31, control_id: 7, control_code: "C-0007", framework_item_code: "PR.AA-01" }],
  affected_evidence: [{ id: 41, control_id: 7, control_code: "C-0007", title: "AD rotation export" }],
};

async function mockImpact(page: Page, options: { noPrevious?: boolean } = {}) {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me")
      return route.fulfill({ json: { id: 1, email: "lead@example.com", name: "Lead", role: "grc_lead" } });
    if (url.pathname === "/api/documents/5")
      return route.fulfill({ json: {
        id: 5, title: "Password Policy", doc_type: "policy", status: "active", version: "2.0",
        owner: null, parse_warnings: null, supersedes_id: options.noPrevious ? null : 3,
      } });
    if (url.pathname === "/api/documents/5/clauses")
      return route.fulfill({ json: [] });
    if (url.pathname === "/api/extraction/documents/5/coverage")
      return route.fulfill({ json: {
        counts: { cited: 0, uncited: 0, not_completed: 0 }, clauses: [],
      } });
    if (url.pathname === "/api/documents/5/change-impact") {
      if (options.noPrevious)
        return route.fulfill({ status: 400, json: { message: "该文档没有上一版本" } });
      return route.fulfill({ json: impact });
    }
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url.pathname}` } });
  });
}

test("the document page offers importing a new version", async ({ page }) => {
  await mockImpact(page);
  await page.goto("/documents/5");

  await expect(page.getByRole("button", { name: "Import new version" })).toBeVisible();
});

test("the change impact page shows added, removed and matched clauses", async ({ page }) => {
  await mockImpact(page);
  await page.goto("/documents/5/change-impact");

  await expect(page.getByText("Passwords must not be reused.")).toBeVisible();
  await expect(page.getByText("Passwords rotate every 90 days.")).toBeVisible();
  await expect(page.locator('[data-impact-section="added"] li')).toHaveCount(1);
  await expect(page.locator('[data-impact-section="removed"] li')).toHaveCount(1);
  await expect(page.locator('[data-impact-section="matched"] li')).toHaveCount(1);
  await expect(page.getByText("Text changed")).toBeVisible();
  await page.getByText("Text changed").click();
  await expect(page.getByText("Passwords may be at least 8 characters.")).toBeVisible();
  await expect(page.getByText("Passwords shall be at least 12 characters.")).toBeVisible();
});

test("the change impact page lists what the removed clauses were holding up", async ({ page }) => {
  await mockImpact(page);
  await page.goto("/documents/5/change-impact");

  await expect(page.getByText("C-0007").first()).toBeVisible();
  await expect(page.getByText("PR.AA-01")).toBeVisible();
  await expect(page.getByText("AD rotation export")).toBeVisible();
});

test("a cross-parser comparison warns that its impact list needs review", async ({ page }) => {
  await mockImpact(page);
  await page.route("**/api/documents/5/change-impact", (route) => route.fulfill({
    json: { ...impact, parser_generation_mismatch: true },
  }));
  await page.goto("/documents/5/change-impact");

  await expect(page.getByRole("alert")).toContainText("different parser generations");
});

test("a document with no previous version says so instead of erroring", async ({ page }) => {
  await mockImpact(page, { noPrevious: true });   // 让接口回 400
  await page.goto("/documents/5/change-impact");

  await expect(page.getByText("no previous version")).toBeVisible();
});

function daysFromToday(days: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

const documents = [
  { id: 1, title: "Overdue Policy", review_due_date: daysFromToday(-365), status: "active" },
  { id: 2, title: "Due Soon Standard", review_due_date: daysFromToday(9), status: "active" },
  { id: 3, title: "Fresh Guideline", review_due_date: daysFromToday(400), status: "active" },
  { id: 4, title: "Undated Procedure", review_due_date: null, status: "active" },
];

async function mockDocuments(page: Page, docs: typeof documents) {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me")
      return route.fulfill({ json: { id: 1, email: "lead@example.com", name: "Lead", role: "grc_lead" } });
    if (url.pathname === "/api/settings/usage")
      return route.fulfill({ json: { month_to_date_cost: 0, budget: null, by_task: [] } });
    if (url.pathname === "/api/evidence/stats")
      return route.fulfill({ json: { expired: 0 } });
    if (url.pathname === "/api/documents")
      return route.fulfill({ json: docs });
    if (url.pathname === "/api/documents/coverage")
      return route.fulfill({ json: [] });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url.pathname}` } });
  });
}

test("the overview counts overdue and soon-due policies", async ({ page }) => {
  await mockDocuments(page, documents);
  await page.goto("/");

  const card = page.locator('[data-card="review-due"]');
  await expect(card).toBeVisible();
  await expect(card.getByText("1 overdue")).toBeVisible();
  await expect(card.getByText("1 due within 30 days")).toBeVisible();
});

test("the overview card links into the filtered document list", async ({ page }) => {
  await mockDocuments(page, documents);
  await page.goto("/");

  await page.locator('[data-card="review-due"]').getByRole("link").first().click();

  await expect(page).toHaveURL(/\/documents\?review=overdue/);
});

test("the document list can show only the overdue ones", async ({ page }) => {
  await mockDocuments(page, documents);
  await page.goto("/documents?review=overdue");

  await expect(page.getByText("Overdue Policy")).toBeVisible();
  await expect(page.getByText("Fresh Guideline")).toHaveCount(0);
  await expect(page.getByText("Undated Procedure")).toHaveCount(0);
});

test("documents without a review date are not counted as overdue", async ({ page }) => {
  await mockDocuments(page, documents);
  await page.goto("/");

  // 没填日期 ≠ 逾期。填不填是人的事，系统不替他判。
  await expect(page.locator('[data-card="review-due"]').getByText("1 overdue")).toBeVisible();
});

async function mockFramework(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me")
      return route.fulfill({ json: { id: 1, email: "lead@example.com", name: "Lead", role: "grc_lead" } });
    if (url.pathname === "/api/frameworks")
      return route.fulfill({ json: [{
        id: 7, key: "csf", name_zh: "CSF", name_en: "CSF", version: "2.0",
        source: "nist.gov", item_count: 1, imported_at: "2026-09-09T00:00:00Z",
      }] });
    if (url.pathname === "/api/frameworks/7/tree")
      return route.fulfill({ json: [] });
    if (url.pathname === "/api/frameworks/7/coverage")
      return route.fulfill({ json: [] });
    if (url.pathname === "/api/frameworks/7/gaps")
      return route.fulfill({ json: [] });
    if (url.pathname === "/api/frameworks/7/readiness-package")
      return route.fulfill({
        status: 200,
        contentType: "application/zip",
        headers: { "Content-Disposition": 'attachment; filename="readiness-7.zip"' },
        body: Buffer.from("PK"),
      });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url.pathname}` } });
  });
}

test("exporting a readiness package triggers a zip download", async ({ page }) => {
  await mockFramework(page);
  await page.goto("/frameworks/7");

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export readiness package" }).click();

  expect((await download).suggestedFilename()).toMatch(/\.zip$/);
});

test("a conflict side whose clause cannot be loaded says so", async ({ page }) => {
  // 审的是两边原文是否真打架。一边加载失败时从前只剩一个「Side B」标题，
  // 看起来像是空条款，审核人照样可以点接受。
  await mockConflicts(page);
  await page.route("**/api/clauses/92", (route) =>
    route.fulfill({ status: 404, json: { code: "clause.not_found", message: "条款不存在" } }),
  );
  await page.goto("/review");

  const card = page.locator('[data-proposal-kind="conflict"]');
  await expect(card.getByText("Passwords rotate every 90 days.")).toBeVisible();
  await expect(card.getByRole("alert")).toContainText("Clause not found");
});

// 已被新版替换的旧版没人会去复审，它的到期日也不会再更新——算进来就是一条永远
// 消不掉的「逾期」，而且每换一次版「受控制度文档」就多一份。
test("a superseded version is neither overdue nor a governed document", async ({ page }) => {
  await mockDocuments(page, [
    ...documents,
    { id: 5, title: "Old Overdue Policy", review_due_date: daysFromToday(-30), status: "superseded" },
  ]);
  await page.goto("/");

  const card = page.locator('[data-card="review-due"]');
  await expect(card.getByText("1 overdue")).toBeVisible();

  await page.goto("/documents?review=overdue");
  await expect(page.getByText("Overdue Policy", { exact: true })).toBeVisible();
  await expect(page.getByText("Old Overdue Policy")).toHaveCount(0);
});

test.describe("review dates in a UTC+8 browser early in the morning", () => {
  test.use({ timezoneId: "Asia/Shanghai" });

  // 「今天」原来取的是 UTC 日期：北京时间凌晨，UTC 还是昨天，昨天到期的制度不算逾期。
  test("a policy due yesterday (local) is overdue", async ({ page }) => {
    await page.clock.setFixedTime(new Date("2026-10-02T01:00:00+08:00"));
    await mockDocuments(page, [
      { id: 1, title: "Due Yesterday Policy", review_due_date: "2026-10-01", status: "active" },
    ]);
    await page.goto("/");
    await expect(page.locator('[data-card="review-due"]').getByText("1 overdue")).toBeVisible();
  });
});

// 驾驶舱上，加载失败不能读成「一切正常」。
test("a card whose data failed to load does not say up to date", async ({ page }) => {
  await mockDocuments(page, documents);
  await page.route("**/api/evidence/stats", (route) => route.fulfill({ status: 500, json: { message: "boom" } }));
  await page.route("**/api/documents", (route) => route.fulfill({ status: 500, json: { message: "boom" } }));
  await page.goto("/");

  await expect(page.getByText("Could not load")).toHaveCount(2);
  await expect(page.getByText("Up to date")).toHaveCount(0);
});
