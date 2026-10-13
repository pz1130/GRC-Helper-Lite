import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const ADMIN = { email: "admin@example.com", password: "pw123456" };

const PROVIDERS = [1, 2].map((id) => ({
  id, name: `probe-${id}`, kind: "openai", model: "m", base_url: null,
  enabled: true, is_fallback: false, api_key_masked: "sk-…abcd",
}));

async function openProviders(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Compliance & Governance Cockpit" })).toBeVisible();
  await page.route("**/api/settings/providers", (route) =>
    route.request().method() === "GET" ? route.fulfill({ json: PROVIDERS }) : route.fallback(),
  );
  await page.goto("/settings/providers");
}

function row(page: Page, name: string) {
  // 只取服务商表：路由表每行的下拉里也列着全部服务商名。
  return page.getByRole("row").filter({ has: page.getByRole("button", { name: "Test connection" }) })
    .filter({ hasText: name });
}

test("测试连接失败时说出来，而不是点了没反应", async ({ page }) => {
  await openProviders(page);
  await page.route("**/api/settings/providers/1/test", (route) =>
    route.fulfill({ status: 404, json: { code: "provider.not_found", message: "服务商不存在" } }),
  );

  await row(page, "probe-1").getByRole("button", { name: "Test connection" }).click();

  await expect(row(page, "probe-1")).toContainText("❌");
});

test("同时测两个服务商，先回来的结果不会被后回来的覆盖", async ({ page }) => {
  await openProviders(page);
  // 1 号慢、2 号快：2 号先写进结果，1 号回来时若拿的是点击那一刻的旧快照，
  // 就会把 2 号的结果抹掉。
  await page.route("**/api/settings/providers/1/test", async (route) => {
    await new Promise((done) => setTimeout(done, 500));
    await route.fulfill({ json: { ok: true, capability: "chat", code: "chat_ok" } });
  });
  await page.route("**/api/settings/providers/2/test", (route) =>
    route.fulfill({ json: { ok: true, capability: "chat", code: "chat_ok" } }),
  );

  await row(page, "probe-1").getByRole("button", { name: "Test connection" }).click();
  await row(page, "probe-2").getByRole("button", { name: "Test connection" }).click();

  await expect(row(page, "probe-1")).toContainText("Connected");
  await expect(row(page, "probe-2")).toContainText("Connected");
});
