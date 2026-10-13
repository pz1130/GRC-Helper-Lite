import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/**
 * 「有效期至」含当天：选 10 月 1 日，账号 10 月 1 日全天可用，管理员所在时区的
 * 当天结束后失效。原来把 "2026-10-01" 原样交给后端，存成 UTC 零点——北京时间当天
 * 早上 8 点就进不来，纽约是前一天晚上 8 点。
 */

async function mockUsers(page: Page, users: unknown[] = []) {
  await page.addInitScript(() => localStorage.setItem("grc.token", "mock-token"));
  let sent: Record<string, unknown> | null = null;
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      return route.fulfill({ json: { id: 1, email: "admin@example.com", name: "Admin", role: "admin" } });
    }
    if (url.pathname === "/api/users" && route.request().method() === "POST") {
      sent = route.request().postDataJSON();
      return route.fulfill({ status: 201, json: { id: 9, ...sent, is_active: true } });
    }
    if (url.pathname === "/api/users") return route.fulfill({ json: users });
    return route.fulfill({ json: [] });
  });
  return () => sent;
}

for (const [zone, lastInstant] of [
  ["Asia/Shanghai", "2026-10-01T15:59:59.999Z"],
  ["America/New_York", "2026-10-02T03:59:59.999Z"],
] as const) {
  test.describe(`in ${zone}`, () => {
    test.use({ timezoneId: zone });

    test("the chosen day is the last day of access", async ({ page }) => {
      const sent = await mockUsers(page);
      await page.goto("/settings/users");
      await page.getByPlaceholder("Email").fill("auditor@example.com");
      await page.getByPlaceholder("Name").fill("Auditor");
      await page.getByPlaceholder("Initial password (at least 8 chars)").fill("pw123456789");
      await page.getByLabel("Access until (inclusive)").fill("2026-10-01");
      await page.getByRole("button", { name: "Save" }).click();
      await expect.poll(() => sent()?.expires_at).toBe(lastInstant);
    });

    test("the list shows the day that was chosen", async ({ page }) => {
      await mockUsers(page, [{
        id: 9, email: "auditor@example.com", name: "Auditor", role: "viewer", is_active: true,
        expires_at: lastInstant,
      }]);
      await page.goto("/settings/users");
      await expect(page.getByRole("row", { name: /auditor@example.com/ })).toContainText("2026-10-01");
    });
  });
}
