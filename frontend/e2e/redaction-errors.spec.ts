import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/**
 * 脱敏决定哪些内容在发给模型之前被遮住。这一页原来所有失败都是沉默的：
 * 规则存不进去（比如正则写错被后端拒绝）点了没反应；预览失败是一个没人接的
 * Promise；规则列表加载失败时是一张空表，看起来像「没有任何脱敏规则」。
 */

async function mockRedaction(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      return route.fulfill({ json: { id: 1, email: "admin@example.com", name: "Admin", role: "admin" } });
    }
    if (url.pathname === "/api/settings/redaction" && route.request().method() === "GET") {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: [] });
  });
}

test("a rule the server refuses says why", async ({ page }) => {
  await mockRedaction(page);
  await page.route("**/api/settings/redaction", (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 422, json: { code: "bad_request", message: "正则无效：unterminated character set" } })
    : route.fallback());
  await page.goto("/settings/redaction");
  await page.getByPlaceholder("Pattern").fill("[unclosed");
  await page.getByPlaceholder("Replacement prefix, e.g. ORG").fill("X");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
});

test("rules that fail to load are not shown as an empty rule set", async ({ page }) => {
  await mockRedaction(page);
  await page.route("**/api/settings/redaction", (route) => route.request().method() === "GET"
    ? route.fulfill({ status: 500, json: { code: "internal", message: "boom" } })
    : route.fallback());
  await page.goto("/settings/redaction");
  await expect(page.getByRole("alert")).toBeVisible();
});

test("a failed preview says so, and an old result does not stand in for the new text", async ({ page }) => {
  await mockRedaction(page);
  let fail = false;
  await page.route("**/api/settings/redaction/preview", (route) => fail
    ? route.fulfill({ status: 500, json: { code: "internal", message: "boom" } })
    : route.fulfill({ json: { redacted: "host [[IP_1]]", hits: { IP: 1 } } }));
  await page.goto("/settings/redaction");

  await page.getByLabel("Send preview input").fill("host 10.0.0.1");
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.locator("pre")).toContainText("[[IP_1]]");

  fail = true;
  await page.getByLabel("Send preview input").fill("host 10.0.0.1 and 10.0.0.2");
  await expect(page.locator("pre")).toHaveCount(0);
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.locator("pre")).toHaveCount(0);
});
