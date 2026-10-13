import { expect, test } from "@playwright/test";

// 使用合成响应验证状态与外发开关；不连接判断服务。
async function session(page: import("@playwright/test").Page, role = "admin") {
  await page.addInitScript(() => localStorage.setItem("grc.token", "test-token"));
  await page.route("**/api/auth/me", (r) => r.fulfill({ json: { id: 1, email: "reviewer@example.com", name: "Reviewer", role } }));
  await page.route("**/api/proposals/stats", (r) => r.fulfill({ json: { pending: 0, by_kind: {} } }));
  await page.route("**/api/health", (r) => r.fulfill({ json: { status: "ok", database: "ok", version: "test" } }));
}

const rules = {
  mode: "rules", reasons: ["disabled"], clause_count: 2, model_count: 0, rule_fallback_count: 2,
  requirements: 1, unlinked_requirements: 1, model: null, scanned_at: null,
  rows: [{ clause_id: 1, citation_label: "1.1", heading_path: "Access", probability: null, rule_match: true, has_control: false }],
};

async function document(page: import("@playwright/test").Page) {
  await page.route("**/api/documents/1", (r) => r.fulfill({ json: { id: 1, title: "Example policy", doc_type: "policy", status: "active", version: "1", parse_warning_codes: [] } }));
  await page.route("**/api/documents/1/clauses", (r) => r.fulfill({ json: [{ id: 1, heading: "Access", citation_label: "1.1", text: "Operators must review permissions.", children: [], level: 1 }] }));
  await page.route("**/api/extraction/documents/1/coverage", (r) => r.fulfill({ json: { counts: { cited: 0, uncited: 1, not_completed: 0 }, clauses: [] } }));
}

test("判断角色默认关闭，保存与启用是显式操作", async ({ page }) => {
  await session(page);
  const provider = { id: 7, name: "Example decision", kind: "jev", model: "jev-1.13.0", enabled: true, capabilities: ["decision"], api_key_masked: "…test" };
  await page.route("**/api/settings/providers", (r) => r.fulfill({ json: [provider] }));
  await page.route("**/api/settings/routing", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/settings/routing/tasks", (r) => r.fulfill({ json: [] }));
  let saved = { enabled: false, provider_config_id: null as number | null };
  await page.route("**/api/settings/decision-model", (r) => {
    if (r.request().method() === "PUT") saved = r.request().postDataJSON();
    return r.fulfill({ json: { ...saved, provider_kinds: ["jev"] } });
  });
  await page.goto("/settings/providers");
  const panel = page.getByRole("region", { name: "Decision model (optional)" });
  await expect(panel.getByLabel("Enable manual decision scans")).not.toBeChecked();
  await panel.getByLabel("Decision provider").selectOption("7");
  await panel.getByLabel("Enable manual decision scans").check();
  await panel.getByRole("button", { name: "Save decision model" }).click();
  await expect(panel.getByText("Decision model settings saved.")).toBeVisible();
  expect(saved).toEqual({ enabled: true, provider_config_id: 7 });
  await panel.getByLabel("Enable manual decision scans").uncheck();
  await panel.getByRole("button", { name: "Save decision model" }).click();
  await expect.poll(() => saved.enabled).toBe(false);
});

test("浏览不扫描；失败时明确显示规则回退", async ({ page }) => {
  await session(page);
  await document(page);
  let posts = 0;
  await page.route("**/api/documents/1/requirement-scan", (r) => {
    if (r.request().method() === "POST") posts++;
    return r.fulfill({ json: { ...rules, reasons: posts ? ["call_failed"] : ["not_run"] } });
  });
  await page.goto("/documents/1");
  const panel = page.getByRole("region", { name: "Requirement review scan" });
  await expect(panel.getByText("Keyword rules only", { exact: true })).toBeVisible();
  expect(posts).toBe(0);
  await panel.getByRole("button", { name: "Run decision scan" }).click();
  await expect(panel.getByText(/model call failed or returned invalid results/)).toBeVisible();
  expect(posts).toBe(1);
  await expect(panel.getByText(/2 using rules only/)).toBeVisible();
});

test("关闭时按钮禁用，只读用户没有外发入口", async ({ page }) => {
  await session(page, "viewer");
  await document(page);
  await page.route("**/api/documents/1/requirement-scan", (r) => r.fulfill({ json: rules }));
  await page.goto("/documents/1");
  const panel = page.getByRole("region", { name: "Requirement review scan" });
  await expect(panel.getByText(/Decision model is off/)).toBeVisible();
  await expect(panel.getByRole("button", { name: "Run decision scan" })).toHaveCount(0);
});
