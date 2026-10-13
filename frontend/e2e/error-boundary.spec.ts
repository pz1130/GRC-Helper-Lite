import { expect, test } from "@playwright/test";

const ADMIN = { email: "admin@example.com", password: "pw123456" };

/**
 * Error Boundary 的直接验证。
 *
 * 这条测试故意把一个接口的返回改成错形状，逼页面在渲染期抛错。没有兜底时
 * React 会卸载整棵树、`#root` 变成空字符串（2026-09-20 上线前测试撞到的 6 个
 * 白屏就是这个形态）。有兜底时该看到一块"这个页面没能显示出来"，而导航还在。
 *
 * 用真实登录态而不是演示模式：演示模式不发请求，route 拦不到。
 */
test("一个接口返回错形状时，只塌一块，不塌整站", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Compliance & Governance Cockpit" })).toBeVisible();

  // statement 缺失 → Controls.tsx 的 `c.statement.slice(...)` 在渲染期抛 TypeError
  await page.route("**/api/controls*", (route) =>
    route.fulfill({ status: 200, json: [{ id: 1, code: "AC-1", title: "缺字段的控制点", category: null }] }),
  );
  await page.goto("/controls");

  await expect(page.getByText("This page could not be displayed")).toBeVisible();
  // 导航还活着——人能走去别的页面，而不是只剩刷新键
  await expect(page.getByRole("navigation")).toBeVisible();
  const rootHtml = await page.evaluate(() => document.getElementById("root")?.innerHTML ?? "");
  expect(rootHtml.length, "#root 被整棵卸载了，兜底没生效").toBeGreaterThan(0);

  // 换一页要能自动恢复：resetKey 跟着路径走，否则人被钉死在错误页上
  await page.unroute("**/api/controls*");
  await page.goto("/documents");
  await expect(page.getByText("This page could not be displayed")).toHaveCount(0);
});
