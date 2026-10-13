import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/** 只读页面上，加载失败原来都是空白：检索看起来像没点到，审计日志看起来像没有记录。 */

async function mockSession(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      return route.fulfill({ json: { id: 1, email: "admin@example.com", name: "Admin", role: "admin" } });
    }
    if (url.pathname === "/api/index/status") return route.fulfill({ json: { model: "", total: 0, embedded: 0, pending: 0 } });
    return route.fulfill({ json: [] });
  });
}

test("a failed search says so", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/search**", (route) => route.fulfill({ status: 500, json: { message: "boom" } }));
  await page.goto("/search");
  await page.getByRole("textbox").first().fill("access review");
  await page.getByRole("textbox").first().press("Enter");
  await expect(page.getByRole("alert")).toBeVisible();
});

test("an audit log that failed to load is not an empty audit log", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/audit-log**", (route) => route.fulfill({ status: 500, json: { message: "boom" } }));
  await page.goto("/settings/audit-log");
  await expect(page.getByRole("alert")).toBeVisible();
});

test("the audit log says when it shows only the latest entries", async ({ page }) => {
  await mockSession(page);
  const entries = Array.from({ length: 200 }, (_, i) => ({
    id: i + 1, at: "2026-09-01T00:00:00Z", user_id: 1, action: "control.update",
    entity_type: "Control", entity_id: String(i + 1), before: null, after: null,
  }));
  await page.route("**/api/audit-log**", (route) => route.fulfill({ json: entries }));
  await page.goto("/settings/audit-log");
  await expect(page.getByRole("note")).toContainText("latest 200");
});
