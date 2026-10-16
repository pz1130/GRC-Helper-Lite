import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/**
 * 用户原来不能自己改密码，只能找管理员重设（KNOWN_ISSUES K14）。现在账户页可以改：
 * 改完这个会话换上新令牌继续用，其他设备上的会话由后端作废。
 */

async function mockApi(page: Page, passwordStatus = 200) {
  await page.addInitScript(() => localStorage.setItem("grc.token", "old-token"));
  const calls: { body: Record<string, unknown>; token: string | null }[] = [];
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      return route.fulfill({ json: { id: 3, email: "lead@example.com", name: "Lead", role: "grc_lead" } });
    }
    if (url.pathname === "/api/auth/password") {
      calls.push({ body: route.request().postDataJSON(), token: route.request().headers()["authorization"] ?? null });
      if (passwordStatus !== 200) {
        return route.fulfill({ status: passwordStatus, json: { message: "当前密码不正确", code: "auth.current_password_incorrect" } });
      }
      return route.fulfill({ json: { access_token: "new-token", token_type: "bearer", user: { id: 3, email: "lead@example.com", name: "Lead", role: "grc_lead" } } });
    }
    return route.fulfill({ json: [] });
  });
  return calls;
}

test("the account page is reachable from the user name in the sidebar", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  // 图标轨底部也有一个头像链接，这里点的是侧边面板里带名字的那个
  await page.getByRole("complementary").getByRole("link", { name: "Account settings" }).click();
  await expect(page).toHaveURL(/\/account$/);
  await expect(page.getByRole("heading", { name: "Change password" })).toBeVisible();
});

test("changing the password keeps this session on the new token", async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto("/account");
  await page.getByLabel("Current password").fill("pw123456");
  await page.getByLabel("New password", { exact: true }).fill("new-pass-1");
  await page.getByLabel("Confirm new password").fill("new-pass-1");
  await page.getByRole("button", { name: "Change password" }).click();

  await expect.poll(() => calls.map((c) => c.body)).toEqual([
    { current_password: "pw123456", new_password: "new-pass-1" },
  ]);
  await expect(page.getByRole("status")).toContainText("Password changed");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("grc.token"))).toBe("new-token");
  await expect(page.getByLabel("Current password")).toHaveValue("");
});

test("a mismatched confirmation is caught before anything is sent", async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto("/account");
  await page.getByLabel("Current password").fill("pw123456");
  await page.getByLabel("New password", { exact: true }).fill("new-pass-1");
  await page.getByLabel("Confirm new password").fill("new-pass-2");
  await page.getByRole("button", { name: "Change password" }).click();

  await expect(page.getByRole("alert")).toContainText("do not match");
  expect(calls).toEqual([]);
});

test("a wrong current password is reported and the user stays signed in", async ({ page }) => {
  await mockApi(page, 400);
  await page.goto("/account");
  await page.getByLabel("Current password").fill("wrong");
  await page.getByLabel("New password", { exact: true }).fill("new-pass-1");
  await page.getByLabel("Confirm new password").fill("new-pass-1");
  await page.getByRole("button", { name: "Change password" }).click();

  await expect(page.getByRole("alert")).toContainText("current password is incorrect");
  await expect(page).toHaveURL(/\/account$/);
  expect(await page.evaluate(() => localStorage.getItem("grc.token"))).toBe("old-token");
});
