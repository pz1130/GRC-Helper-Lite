import { expect, test } from "@playwright/test";

const ADMIN = { email: "admin@example.com", password: "pw123456" };

/**
 * 查不到的 i18n 键不许原样显示给用户。
 *
 * 全站 50+ 处 `t(`prefix.${value}`)`，value 多半来自接口。这条测试把一个枚举
 * 值换成词条里没有的，验证界面显示的是可读的回退值，而不是
 * `techAssets.categories.xxx` 这样的内部路径（i18next 的默认行为就是原样吐键）。
 *
 * 用真实登录态：演示模式不发请求，route 拦不到。
 */
test("词条里没有的枚举值不会把内部键泄漏到界面", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Compliance & Governance Cockpit" })).toBeVisible();

  await page.route("**/api/tech-assets*", (route) =>
    route.fulfill({
      status: 200,
      json: [{
        id: 1, name: "资产 A", category: "quantum_widget", vendor: "V",
        environment: undefined, owner_user_id: null, scope_note: "",
        status: "active", created_at: "2026-09-01T00:00:00Z",
      }],
    }),
  );
  await page.goto("/tech-assets");
  await expect(page.getByText("资产 A")).toBeVisible();

  const text = await page.evaluate(() => document.body.innerText);
  expect(text, "原始 i18n 键漏到界面上了").not.toContain("techAssets.categories.");
  expect(text, "原始 i18n 键漏到界面上了").not.toContain("techAssets.environments.");
  expect(text, "undefined 漏到界面上了").not.toMatch(/undefined/);
  // 回退成最后一段：比内部路径可读，也不暴露键的结构
  expect(text).toContain("quantum_widget");
});
