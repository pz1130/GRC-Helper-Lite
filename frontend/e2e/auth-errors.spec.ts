import { expect, test } from "@playwright/test";

test("failed real login does not enter the demo session", async ({ page }) => {
  await page.route("**/api/auth/login", (route) => route.fulfill({ status: 401, json: { message: "Invalid credentials" } }));
  await page.goto("/login");
  await page.getByLabel("Email").fill("admin@example.com");
  await page.getByLabel("Password").fill("pw123456");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("grc.token"))).toBeNull();
});

test("offline demo remains an explicit option", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "Enter demo" }).click();
  await expect(page).not.toHaveURL(/\/login$/);
  expect(await page.evaluate(() => localStorage.getItem("grc.token"))).toBe("demo-mock-token");
});

// 登录失败原来一律显示「邮箱或密码不正确」：被限流的人会继续重试，服务宕机时
// 所有人都以为自己密码错了。
for (const [label, reply, expected] of [
  ["rate limited", { status: 429, json: { code: "auth.too_many_attempts", message: "尝试次数过多" } }, "Too many login attempts"],
  ["server down", { status: 503, json: { code: "internal", message: "服务不可用" } }, null],
] as const) {
  test(`a ${label} login does not claim the password is wrong`, async ({ page }) => {
    await page.route("**/api/auth/login", (route) => route.fulfill(reply));
    await page.goto("/login");
    await page.getByLabel("Email").fill("admin@example.com");
    await page.getByLabel("Password").fill("pw123456");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("alert")).not.toContainText("Incorrect email or password");
    if (expected) await expect(page.getByRole("alert")).toContainText(expected);
  });
}

test("a network failure on login says the request failed", async ({ page }) => {
  await page.route("**/api/auth/login", (route) => route.abort("connectionrefused"));
  await page.goto("/login");
  await page.getByLabel("Email").fill("admin@example.com");
  await page.getByLabel("Password").fill("pw123456");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Request failed");
});
