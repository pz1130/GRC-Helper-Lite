import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// 每一个 API 请求都被拦截：这些用例从不碰应用库。
async function mockSession(page: Page, role = "grc_lead") {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    if (new URL(route.request().url()).pathname === "/api/auth/me") {
      await route.fulfill({ json: { id: 1, email: `${role}@example.com`, name: "User", role } });
    } else {
      await route.fulfill({ status: 500, json: { message: `Unexpected API: ${route.request().url()}` } });
    }
  });
}

const relationProposal = {
  id: 31,
  kind: "relation",
  payload: {
    from_control_id: 1,
    to_control_id: 2,
    relation_type: "depends_on",
    from_quote: "Approved by the CAB",
    to_quote: "Follow the plan",
    rationale: "ordering",
    confidence: 0.8,
  },
  citations: [],
  confidence: 0.8,
  document_id: null,
  status: "pending",
  bulk_acceptable: false,
  relation_context: {
    relation_type: "depends_on",
    from: { id: 1, code: "C-0001", title: "Approval", statement: "Changes are Approved by the CAB before release.", sources: [{ clause_id: 11, citation_label: "4.2", heading_path: "Change Management › Approval", document_id: 3, document_title: "Acme Change Management Procedure v2.0" }] },
    to: { id: 2, code: "C-0002", title: "Implementation", statement: "Follow the plan after CAB approval.", sources: [] },
  },
};

async function mockRelationQueue(page: Page) {
  await page.route("**/api/proposals**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/proposals/stats") {
      return route.fulfill({ json: { pending: 1, by_kind: { relation: 1 } } });
    }
    return route.fulfill({ json: [relationProposal] });
  });
}

test("relation proposals show both controls side by side with highlighted quotes", async ({ page }) => {
  await mockSession(page);
  await mockRelationQueue(page);
  await page.goto("/review?kind=relation");
  const card = page.locator("#proposal-31");
  await expect(card.getByRole("heading", { name: "Starting control · C-0001 Approval" })).toBeVisible();
  await expect(card.getByRole("heading", { name: "Ending control · C-0002 Implementation" })).toBeVisible();
  await expect(card.getByText("Changes are Approved by the CAB before release.")).toBeVisible();
  await expect(card.getByText("Follow the plan after CAB approval.")).toBeVisible();
  await expect(card.locator("mark").filter({ hasText: "Approved by the CAB" })).toHaveCount(1);
  await expect(card.locator("mark").filter({ hasText: "Follow the plan" })).toHaveCount(1);
});

test("relation type is shown as a translated label rather than depends_on", async ({ page }) => {
  await mockSession(page);
  await mockRelationQueue(page);
  await page.goto("/review?kind=relation");
  const card = page.locator("#proposal-31");
  await expect(card.getByText("Depends on", { exact: true })).toBeVisible();
  await expect(card).not.toContainText("depends_on");
});

test("contributors do not see the run relation inference button", async ({ page }) => {
  await mockSession(page, "contributor");
  await mockRelationQueue(page);
  await page.goto("/review");
  await expect(page.getByText("C-0001", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run relation inference" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Backfill control embeddings" })).toHaveCount(0);
});

test("relation proposals below the threshold have a disabled batch checkbox", async ({ page }) => {
  await mockSession(page);
  await mockRelationQueue(page);
  await page.goto("/review?kind=relation");
  await expect(page.locator("#proposal-31")).toBeVisible();
  await expect(page.getByLabel("Select proposal 31")).toBeDisabled();
});

test("automation preview shows impact before any write", async ({ page }) => {
  await mockSession(page);
  await mockRelationQueue(page);
  await page.route("**/api/proposals/auto-process/preview", async (route) => {
    await route.fulfill({ json: {
      scanned: 482,
      truncated: false,
      by_tier: { auto: 20, sample: 48, manual: 2, deferred: 412 },
      by_kind: { mapping: { auto: 15, sample: 40, manual: 2, deferred: 345 } },
      by_framework: { "nist-csf-2.0": { auto: 8, sample: 12, manual: 1, deferred: 100 } },
      by_reason: {},
      auto_items: [{ id: 41, kind: "mapping", confidence: 0.96, source: "C-0001", target: "PR.AA-01" }],
      sample_items: [{ id: 42, kind: "relation", confidence: 0.81, source: "C-0001", target: "C-0002" }],
      estimated_changes: { mappings: 15, relations: 5 },
    } });
  });
  const executed: unknown[] = [];
  await page.route("**/api/proposals/auto-process", async (route) => {
    executed.push(route.request().postDataJSON());
    await route.fulfill({ json: { scanned: 1, accepted: 1, skipped: 0, auto: 1, sample: 0, manual: 0, deferred: 0 } });
  });
  await page.goto("/review");

  await page.getByRole("button", { name: "Preview automation" }).click();

  const preview = page.getByRole("region", { name: "Automation preview" });
  await expect(preview).toContainText("Execution is expected to add 15 formal mappings and 5 formal relationships.");
  await expect(preview).toContainText("C-0001 → PR.AA-01");
  // 执行的是列出来给人看过的那张清单，不是「此刻队列里合格的全部」：
  // 自动档统计是 20，但清单上只有 #41，按钮与确认框都按清单算。
  const run = preview.getByRole("button", { name: "Run automation (1)" });
  await expect(run).toBeVisible();
  page.once("dialog", (dialog) => void dialog.accept());
  await run.click();
  await expect.poll(() => executed.length).toBe(1);
  expect(executed[0]).toEqual({ ids: [41] });
});

// 扫描到上限时，预览上的数字只覆盖了一部分队列——原来界面上不说。
test("automation preview says when it covers only part of the queue", async ({ page }) => {
  await mockSession(page);
  await mockRelationQueue(page);
  await page.route("**/api/proposals/auto-process/preview", (route) => route.fulfill({ json: {
    scanned: 4000, truncated: true,
    by_tier: { auto: 1, sample: 0, manual: 0, deferred: 3999 }, by_kind: {}, by_framework: {}, by_reason: {},
    auto_items: [{ id: 41, kind: "mapping", confidence: 0.96, source: "C-0001", target: "PR.AA-01" }],
    sample_items: [], estimated_changes: { mappings: 1, relations: 0 },
  } }));
  await page.goto("/review");
  await page.getByRole("button", { name: "Preview automation" }).click();
  const preview = page.getByRole("region", { name: "Automation preview" });
  await expect(preview.getByRole("note")).toContainText("4000 proposals scanned");
});

test("relation cards show a payload textarea while editing", async ({ page }) => {
  await mockSession(page);
  await mockRelationQueue(page);
  await page.goto("/review?kind=relation");
  const card = page.locator("#proposal-31");
  await card.getByRole("button", { name: "Edit and accept" }).click();
  await expect(card.getByLabel("Proposed content (JSON)")).toBeVisible();
  await expect(card.getByRole("heading", { name: "Starting control · C-0001 Approval" })).toBeVisible();
  await expect(card.getByRole("heading", { name: "Ending control · C-0002 Implementation" })).toBeVisible();
});

test("a relation whose control was deleted still shows its payload and stays editable", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/proposals**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/proposals/stats") {
      return route.fulfill({ json: { pending: 1, by_kind: { relation: 1 } } });
    }
    // 一端控制点被删：后端给不出 relation_context，卡片不能因此变成空白。
    return route.fulfill({ json: [{ ...relationProposal, relation_context: null }] });
  });
  await page.goto("/review?kind=relation");
  const card = page.locator("#proposal-31");
  await expect(card.getByText("Depends on", { exact: true })).toBeVisible();
  await expect(card.getByText(/no longer exists/)).toBeVisible();
  await expect(card.getByText(/"from_quote": "Approved by the CAB"/)).toBeVisible();
  await card.getByRole("button", { name: "Edit and accept" }).click();
  await expect(card.getByLabel("Proposed content (JSON)")).toBeVisible();
});

test("a relation proposal shows the reasoning and each control's source", async ({ page }) => {
  await mockSession(page);
  await mockRelationQueue(page);
  await page.goto("/review?kind=relation");
  const card = page.locator("#proposal-31");
  await expect(card.getByText("ordering")).toBeVisible();
  await expect(card.getByText("Acme Change Management Procedure v2.0")).toBeVisible();
  await expect(card.getByRole("link", { name: "4.2" })).toHaveAttribute("href", "/documents/3#clause-11");
});

test("a relation card says both sides are internal controls", async ({ page }) => {
  await mockSession(page);
  await mockRelationQueue(page);
  await page.goto("/review?kind=relation");
  await expect(page.locator("#proposal-31")).toContainText("Both sides are your internal controls");
});

test("the default queue includes deferred proposals counted in the badge", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/proposals**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/stats")) return route.fulfill({ json: { pending: 1, by_kind: { relation: 1 } } });
    return route.fulfill({ json: url.searchParams.get("actionable_only") === "true" ? [] : [{ ...relationProposal, review_tier: "deferred" }] });
  });
  await page.goto("/review");
  await expect(page.getByLabel("Show deferred unverified items")).toBeChecked();
  await expect(page.locator("#proposal-31")).toBeVisible();
  await page.getByLabel("Show deferred unverified items").uncheck();
  await expect(page.locator("#proposal-31")).toHaveCount(0);
});
