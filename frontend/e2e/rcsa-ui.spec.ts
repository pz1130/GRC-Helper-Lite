import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

// RCSA 界面边界：全部接口 mock，不需要后端。业务流程本身由 rcsa-flow.spec.ts 在真实后端上走。
type Handler = (route: Route, url: URL) => Promise<void> | void;

const LEAD = { id: 1, email: "lead@example.com", name: "Lead", role: "grc_lead" };
const ASSESSOR = { id: 2, email: "asa@example.com", name: "Asa", role: "contributor" };
const PEOPLE = [
  { ...LEAD, can_respond: true, can_review: true, can_manage: true, can_accept_risk: true },
  { ...ASSESSOR, can_respond: true, can_review: false, can_manage: false, can_accept_risk: false },
];

function campaign(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id, name: `Acme Q${id}`, framework_id: 1, framework_snapshot: { key: "acme", name: "Acme IT", version: "1" },
    profile_id: 1, profile_snapshot: { key: "default-5x5", revision: 1, horizon: "12 months", bands: [] },
    period_start: "2026-01-01", period_end: "2026-03-31", due_at: "2026-04-30", timezone: "UTC", owner_id: 1,
    status: "active", status_reason: "", predecessor_campaign_id: null, audit_engagement_id: null,
    activated_at: "2026-01-02T00:00:00Z", closed_at: null, version: 2,
    counts: { total: 2, submitted: 0, approved: 1, excluded: 1 }, ...overrides,
  };
}

const ITEM = {
  id: 7, campaign_id: 1, control_id: 9,
  control_snapshot: { code: "C-0009", title: "Quarterly access review", statement: "Access is reviewed.", owner_user_id: 2 },
  mapping_snapshot: [{ code: "AC-2", title: "Account management", strength: "full" }], source_snapshot: [],
  assignee_id: 2, reviewer_id: 1, status: "draft", current_response_id: 70, provider_type: "internal", version: 3,
};

const RESPONSE = {
  id: 70, item_id: 7, revision: 1, status: "draft", answer_schema: 1, risk_snapshot: null,
  submitted_by: null, submitted_at: null, version: 4, evidence: [],
  answer: {
    design: { rating: "effective", rationale: "Covers every account" }, operating: { rating: null, rationale: "" },
    assessment_period_start: null, assessment_period_end: null, limitations: "", independent_test_status: null,
    independent_test_reason: "", not_assessed_reason: "", evidence_followup: null, no_risk_rationale: "",
    evidence_limitations_acknowledged: false,
  },
};

async function mockApi(page: Page, user: typeof LEAD, routes: Record<string, Handler>, seen: string[] = []) {
  await page.addInitScript(() => localStorage.setItem("grc.token", "mock-token"));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    seen.push(`${route.request().method()} ${url.pathname}`);
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: user });
    if (url.pathname === "/api/proposals/stats") return route.fulfill({ json: { pending: 0, by_kind: {} } });
    if (url.pathname === "/api/health") return route.fulfill({ json: { status: "ok", database: "ok", version: "test" } });
    if (url.pathname === "/api/rcsa/people") return route.fulfill({ json: PEOPLE });
    const handler = routes[url.pathname];
    if (handler) return handler(route, url);
    if (url.pathname === "/api/rcsa/status") return route.fulfill({ json: { enabled: true } });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url.pathname}` } });
  });
}

function itemRoutes(patch: Handler): Record<string, Handler> {
  return {
    "/api/rcsa/items/7": (route) => route.fulfill({ json: ITEM }),
    "/api/rcsa/campaigns/1": (route) => route.fulfill({ json: campaign(1) }),
    "/api/rcsa/items/7/responses": (route) => route.fulfill({ json: [RESPONSE] }),
    "/api/rcsa/items/7/reviews": (route) => route.fulfill({ json: [] }),
    "/api/rcsa/items/7/risks": (route) => route.fulfill({ json: [] }),
    "/api/evidence": (route) => route.fulfill({ json: [] }),
    "/api/rcsa/responses/70": patch,
  };
}

test("with the module off there is no RCSA entry and the page says so", async ({ page }) => {
  await mockApi(page, LEAD, { "/api/rcsa/status": (route) => route.fulfill({ json: { enabled: false } }) });
  await page.goto("/rcsa");

  await expect(page.getByText("RCSA is not enabled")).toBeVisible();
  await expect(page.getByRole("link", { name: "RCSA", exact: true })).toHaveCount(0);
});

test("a stale save shows the conflict and offers a reload instead of overwriting", async ({ page }) => {
  await mockApi(page, ASSESSOR, itemRoutes((route) => route.fulfill({
    status: 409, json: { code: "rcsa.version_conflict", message: "冲突", params: { expected: 4, current: 5 } },
  })));
  await page.goto("/rcsa/items/7");

  const operating = page.getByRole("group", { name: "Operating effectiveness" });
  await operating.getByLabel("Rationale").fill("Two reviews closed late");
  await page.getByRole("button", { name: "Save" }).click();

  await expect(page.getByRole("alert").filter({ hasText: "Someone else changed this record" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reload" })).toBeVisible();
});

test("leaving with unsaved changes asks first", async ({ page }) => {
  await mockApi(page, ASSESSOR, itemRoutes((route) => route.fulfill({ json: RESPONSE })));
  await page.goto("/rcsa/items/7");
  await page.getByRole("group", { name: "Operating effectiveness" }).getByLabel("Rationale").fill("Unsaved text");

  let asked = "";
  page.once("dialog", async (dialog) => { asked = dialog.message(); await dialog.dismiss(); });
  await page.getByRole("link", { name: "Acme Q1" }).click();

  expect(asked).toContain("unsaved changes");
  await expect(page).toHaveURL(/\/rcsa\/items\/7$/);
});

test("submitting an incomplete answer lists what is missing", async ({ page }) => {
  const routes = itemRoutes((route) => route.fulfill({ json: RESPONSE }));
  routes["/api/rcsa/responses/70/submit"] = (route) => route.fulfill({
    status: 409, json: {
      code: "rcsa.submission_incomplete", message: "不完整",
      params: { problems: ["rcsa.problem.operating_rating", "rcsa.problem.risks_or_rationale"] },
    },
  });
  await mockApi(page, ASSESSOR, routes);
  await page.goto("/rcsa/items/7");
  await page.getByRole("button", { name: "Submit for review" }).click();

  const alert = page.getByRole("alert");
  await expect(alert.getByText("Rate operating effectiveness")).toBeVisible();
  await expect(alert.getByText("Add at least one risk scenario, or explain why there is none")).toBeVisible();
});

test("campaign lists page through results and say when a filter matches nothing", async ({ page }) => {
  await mockApi(page, LEAD, {
    "/api/rcsa/my-work": (route) => route.fulfill({ json: { rows: [] } }),
    "/api/rcsa/campaigns": (route, url) => {
      if (url.searchParams.get("status") === "cancelled") return route.fulfill({ json: { items: [], total: 0 } });
      const offset = Number(url.searchParams.get("offset"));
      const items = Array.from({ length: offset ? 5 : 25 }, (_, i) => campaign(offset + i + 1));
      return route.fulfill({ json: { items, total: 30 } });
    },
  });
  await page.goto("/rcsa");

  await expect(page.getByText("1–25 of 30")).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("26–30 of 30")).toBeVisible();
  await page.getByLabel("Filter by status").selectOption("cancelled");
  await expect(page.getByText("No campaigns with this status")).toBeVisible();
  await expect(page.getByText("Nothing waiting for you")).toBeVisible();
});

test("an open campaign's report is marked Draft and keeps its gaps visible", async ({ page }) => {
  await mockApi(page, LEAD, {
    "/api/rcsa/campaigns/1/report": (route) => route.fulfill({ json: {
      watermark: "draft", source: "live", generated_at: "2026-04-01T00:00:00Z",
      campaign: { id: 1, name: "Acme Q1", status: "active", period_start: "2026-01-01", period_end: "2026-03-31",
        due_at: "2026-04-30", closed_at: null, framework: null,
        profile: { key: "default-5x5", revision: 1, horizon: "12 months", bands: null } },
      scope: { total: 3, approved: 1, excluded: 1, submitted: 1, in_progress: 0 },
      exclusions: [{ control_code: "C-0003", reason: "Decommissioned in January" }],
      not_assessed: [{ control_code: "C-0002", ratings: ["operating"], reason: "Logs not retained", follow_up_due: "2026-06-30" }],
      independent_tests: { provided: 0, not_provided: 1, not_applicable: 0 },
      scenario_bands: { inherent: { High: 1 }, residual: { Moderate: 1 } }, dispositions: { reduce: 1 },
      acceptances: [], actions: { open: 1, overdue: 1, verified: 0, cancelled: 0 }, limitations: [],
      acknowledgements: null, items: [], highest_residual_band: "Moderate", ai_assistance: "not_enabled",
      comparison: {
        previous: { id: 9, name: "Acme Q4", status: "closed" }, comparable: false, reason: "profile_changed",
        scenarios: [{ control_code: "C-0001", scenario_key: "stale-access", status: "not_directly_comparable",
          previous_residual_score: 12, previous_residual_band: "High", current_residual_score: 6, current_residual_band: "Moderate" }],
      },
      current_follow_up: { actions: [{ id: 5, title: "Automate leavers", control_code: "C-0001", status: "open", due_at: "2026-02-01", overdue: true }], acceptances: [] },
    } }),
  });
  await page.goto("/rcsa/campaigns/1/report");

  await expect(page.getByRole("note")).toHaveText("Draft");
  await expect(page.getByText("Decommissioned in January")).toBeVisible();
  await expect(page.getByText(/Logs not retained/)).toBeVisible();
  await expect(page.getByText("Not provided: 1")).toBeVisible();
  await expect(page.getByText("Overdue since 2026-02-01")).toBeVisible();
  await expect(page.getByRole("cell", { name: "Not directly comparable" })).toBeVisible();
  await expect(page.getByText(/nothing in this report was generated by a model/)).toBeVisible();
});

test("browsing RCSA pages never calls a model", async ({ page }) => {
  const seen: string[] = [];
  await mockApi(page, ASSESSOR, {
    ...itemRoutes((route) => route.fulfill({ json: RESPONSE })),
    "/api/rcsa/my-work": (route) => route.fulfill({ json: { rows: [] } }),
    "/api/rcsa/campaigns": (route) => route.fulfill({ json: { items: [campaign(1)], total: 1 } }),
  }, seen);
  await page.goto("/rcsa");
  await expect(page.getByRole("link", { name: "Acme Q1" })).toBeVisible();
  await page.goto("/rcsa/items/7");
  await expect(page.getByRole("heading", { name: /Quarterly access review/ })).toBeVisible();

  expect(seen.filter((call) => /llm|prefill|rank-evidence|decision/.test(call))).toEqual([]);
});

test("the wizard can be completed from the keyboard", async ({ page }) => {
  await mockApi(page, LEAD, {
    "/api/frameworks": (route) => route.fulfill({ json: [{ id: 1, key: "acme", name_en: "Acme IT", version: "1" }] }),
    "/api/rcsa/profiles": (route) => route.fulfill({ json: [{ id: 1, key: "default-5x5", revision: 1, status: "approved" }] }),
  });
  await page.goto("/rcsa/campaigns/new");

  await page.getByLabel("Name").focus();
  await page.keyboard.type("Acme Q2");
  for (const date of ["04012026", "06302026", "07312026"]) {
    await page.keyboard.press("Tab");
    await page.keyboard.type(date);
  }
  await page.keyboard.press("Enter");

  await expect(page.getByLabel("Framework")).toBeVisible();
});
