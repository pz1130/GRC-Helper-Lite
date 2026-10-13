import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/**
 * 用户忘了密码，原来只能走 `PATCH /api/users/{id}` 或 CLI——「用户管理」页只能改角色与
 * 启用状态（KNOWN_ISSUES G7）。现在每行有「重设密码」。
 */

const USER = {
  id: 9, email: "lead@example.com", name: "Lead", role: "grc_lead", is_active: true, expires_at: null,
};

async function mockUsers(page: Page, patchStatus = 200) {
  await page.addInitScript(() => localStorage.setItem("grc.token", "mock-token"));
  const patches: Record<string, unknown>[] = [];
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      return route.fulfill({ json: { id: 1, email: "admin@example.com", name: "Admin", role: "admin" } });
    }
    if (url.pathname === `/api/users/${USER.id}` && route.request().method() === "PATCH") {
      patches.push(route.request().postDataJSON());
      if (patchStatus !== 200) {
        return route.fulfill({
          status: patchStatus,
          json: { message: "用户不存在", code: "user.not_found" },
        });
      }
      return route.fulfill({ json: USER });
    }
    if (url.pathname === "/api/users") return route.fulfill({ json: [USER] });
    return route.fulfill({ json: [] });
  });
  return patches;
}

test("an admin can set a new password from the user list", async ({ page }) => {
  const patches = await mockUsers(page);
  await page.goto("/settings/users");
  await page.getByRole("button", { name: "Reset password for lead@example.com" }).click();
  await page.getByLabel("New password for lead@example.com").fill("new-pass-123");
  await page.getByRole("button", { name: "Set password", exact: true }).click();

  // 只发密码：顺手带上别的字段会把别人刚改的角色覆盖回去。
  await expect.poll(() => patches).toEqual([{ password: "new-pass-123" }]);
  await expect(page.getByRole("status")).toContainText("Password reset for lead@example.com");
  await expect(page.getByLabel("New password for lead@example.com")).toHaveCount(0);
});

test("a short password is refused before anything is sent", async ({ page }) => {
  const patches = await mockUsers(page);
  await page.goto("/settings/users");
  await page.getByRole("button", { name: "Reset password for lead@example.com" }).click();
  await page.getByLabel("New password for lead@example.com").fill("short");
  await page.getByRole("button", { name: "Set password", exact: true }).click();

  await expect(page.getByLabel("New password for lead@example.com")).toBeVisible();
  expect(patches).toEqual([]);
});

test("a failed reset says so and keeps the form open", async ({ page }) => {
  const patches = await mockUsers(page, 404);
  await page.goto("/settings/users");
  await page.getByRole("button", { name: "Reset password for lead@example.com" }).click();
  await page.getByLabel("New password for lead@example.com").fill("new-pass-123");
  await page.getByRole("button", { name: "Set password", exact: true }).click();

  await expect.poll(() => patches.length).toBe(1);
  await expect(page.getByRole("alert")).toContainText("User not found");
  await expect(page.getByRole("status")).toHaveCount(0);
  await expect(page.getByLabel("New password for lead@example.com")).toBeVisible();
});

test("cancel closes the form without sending anything", async ({ page }) => {
  const patches = await mockUsers(page);
  await page.goto("/settings/users");
  await page.getByRole("button", { name: "Reset password for lead@example.com" }).click();
  await page.getByLabel("New password for lead@example.com").fill("new-pass-123");
  await page.getByRole("button", { name: "Cancel" }).click();

  await expect(page.getByLabel("New password for lead@example.com")).toHaveCount(0);
  expect(patches).toEqual([]);
});
