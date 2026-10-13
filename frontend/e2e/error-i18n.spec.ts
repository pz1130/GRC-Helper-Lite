import { expect, test } from "@playwright/test";

const ADMIN = { email: "admin@example.com", password: "pw123456" };

/**
 * 后端的业务错误句子是写死的中文，前端原样显示——英文界面上就是中文。
 *
 * 修法是后端多带一个**具体的** code（`control.not_found`，不是通用的
 * `not_found`），前端按 code 查词条、用 `message` 兜底。翻译放在 `ApiError`
 * 的构造里，所以全站几十处 `{error.message}` 一处都不用改。
 *
 * 用真实登录态：演示模式不发请求，route 拦不到。
 */
test("英文界面上，后端的业务错误显示成英文而不是中文", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Compliance & Governance Cockpit" })).toBeVisible();

  await page.route("**/api/controls*", (route) =>
    route.fulfill({
      status: 404,
      json: { code: "control.not_found", message: "控制点不存在" },
    }),
  );
  await page.goto("/controls");

  const alert = page.getByRole("alert").first();
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("Control not found");
  const text = await page.evaluate(() => document.body.innerText);
  expect(text, "英文界面上漏出了后端的中文句子").not.toContain("控制点不存在");
  expect(text, "原始 i18n 键漏到界面上了").not.toContain("errors.control");
});

test("还没迁移的 code 回退到后端句子，而不是显示内部键", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Compliance & Governance Cockpit" })).toBeVisible();

  await page.route("**/api/controls*", (route) =>
    route.fulfill({
      status: 409,
      json: { code: "conflict", message: "还没迁移的那一类错误" },
    }),
  );
  await page.goto("/controls");

  const alert = page.getByRole("alert").first();
  await expect(alert).toContainText("还没迁移的那一类错误");
  const text = await page.evaluate(() => document.body.innerText);
  expect(text, "原始 i18n 键漏到界面上了").not.toContain("errors.conflict");
});

/**
 * 句子里有变量的（"还有 3 条证据在用这个类型"）：光有 code 翻不了，前端拿到
 * `evidence_type.in_use` 也不知道是几条。变量跟着 `params` 一起过来，
 * 由词条自己排版。
 */
test("带变量的错误，英文界面上把值填进英文句子", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Compliance & Governance Cockpit" })).toBeVisible();

  await page.route("**/api/controls*", (route) =>
    route.fulfill({
      status: 409,
      json: {
        code: "evidence_type.in_use",
        message: "还有 3 条证据在用这个类型，请先改掉它们的类型或删除那些证据",
        params: { count: 3 },
      },
    }),
  );
  await page.goto("/controls");

  await expect(page.getByRole("alert").first()).toContainText(
    "3 evidence records still use this type",
  );
  const text = await page.evaluate(() => document.body.innerText);
  expect(text, "变量没填进去，占位符漏到界面上了").not.toContain("{{count}}");
});
