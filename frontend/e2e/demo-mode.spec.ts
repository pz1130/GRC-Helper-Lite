import { expect, test } from "@playwright/test";

/**
 * 离线演示模式的页面级覆盖。
 *
 * 在这之前，演示模式只有一条测试验证"按钮会设置 token"，没有任何一条走进演示
 * 会话翻页面——于是 6 个页面整站白屏一直没人发现（2026-09-20 的上线前测试报告）。
 *
 * 白屏的判据是 #root 被整棵卸载（innerHTML 为空）。这里不断言页面"内容对"，
 * 只断言它**没有塌掉**：演示数据本来就是示例，内容对不对不是这组测试的事。
 */

const DEMO_ROUTES = [
  "/controls",
  "/review",
  "/graph",
  "/risks",
  "/evidence",
  "/frameworks",
  "/tech-assets",
  "/settings/redaction",
  "/settings/users",
];

async function enterDemo(page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "Enter demo" }).click();
  await expect(page).not.toHaveURL(/\/login$/);
}

async function expectNotBlank(page, where: string) {
  const html = await page.evaluate(() => document.getElementById("root")?.innerHTML ?? "");
  expect(html.length, `${where} 整页白屏：#root 被卸载`).toBeGreaterThan(0);
  // 光"不白屏"不够——Error Boundary 自己就能让上面那条过。这条钉的是根因：
  // 页面得真的渲染出来，而不是塌进兜底错误页。
  await expect(
    page.getByText("This page could not be displayed"),
    `${where} 没白屏，但塌进了 Error Boundary 兜底页——根因还在`,
  ).toHaveCount(0);
}

for (const route of DEMO_ROUTES) {
  test(`demo session renders ${route} without a blank page`, async ({ page }) => {
    await enterDemo(page);
    await page.goto(route);
    await page.waitForTimeout(600);
    await expectNotBlank(page, route);
  });
}

test("demo session survives submitting a search", async ({ page }) => {
  await enterDemo(page);
  await page.goto("/search");
  await page.getByRole("textbox").first().fill("access control");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(800);
  await expectNotBlank(page, "/search（提交后）");
});

test("demo session stays signed in while navigating", async ({ page }) => {
  // 演示模式不该有任何请求打到真后端——打了就会 401，401 会清掉 token 把人踢回登录页。
  const leaked: string[] = [];
  await page.route("**/api/**", async (route) => {
    leaked.push(route.request().method() + " " + new URL(route.request().url()).pathname);
    await route.abort();
  });
  await enterDemo(page);
  for (const route of ["/documents", "/frameworks", "/controls"]) {
    await page.goto(route);
    await page.waitForTimeout(400);
  }
  expect(leaked, `演示模式把这些请求漏到了真后端：${leaked.join(", ")}`).toEqual([]);
  expect(await page.evaluate(() => localStorage.getItem("grc.token"))).toBe("demo-mock-token");
});


/**
 * 演示页面不许把内部标识漏给用户看。
 *
 * 两种漏法，同一个病根——mock 对象缺字段：
 *   · 字段是 undefined，被直接渲染 → "#undefined"、"Source: undefined"
 *   · 字段是 undefined，又被拼进 i18n key → t("evidence.statuses.undefined")
 *     查不到，i18next 把原始键当文案吐出来
 * 注意这**不是**"语言包缺翻译"：en.json 里 techAssets.categories 等键都是齐的。
 */
const I18N_KEY_LEAK = /\b(evidence|techAssets|frameworks|controls|review|maturity|risks|settings|documents)\.[a-zA-Z]+\.[a-zA-Z_]+/;

for (const route of ["/frameworks", "/evidence", "/tech-assets", "/controls", "/risks"]) {
  test(`demo session shows no undefined or raw i18n key on ${route}`, async ({ page }) => {
    await enterDemo(page);
    await page.goto(route);
    await page.waitForTimeout(600);
    const text = await page.evaluate(() => document.body.innerText);

    expect(text, `${route} 把 undefined 渲染给用户看了`).not.toMatch(/undefined/);
    const leak = text.match(I18N_KEY_LEAK);
    expect(leak?.[0], `${route} 漏出了原始 i18n key`).toBeUndefined();
  });
}

test("demo session shows framework names, not just version numbers", async ({ page }) => {
  // BUG-10：成熟度的框架下拉只剩 "· 2.0"。根因不在下拉，在 /api/frameworks 的
  // mock 用了 name/code 而真接口是 name_zh/name_en/key。
  await enterDemo(page);
  await page.goto("/maturity");
  await page.waitForTimeout(600);
  const options = await page.locator("select option").allInnerTexts();
  const frameworkOptions = options.filter((o) => /\d+\.\d+/.test(o));
  expect(frameworkOptions.length, "下拉里没有框架选项").toBeGreaterThan(0);
  for (const option of frameworkOptions) {
    expect(option.replace(/[·\s\d.]/g, "").length, `下拉项 "${option}" 只有版本号，没有框架名`).toBeGreaterThan(0);
  }
});

test("demo session lists framework names and item counts", async ({ page }) => {
  // BUG-05 的两个现象里，"名称列全空"和"条目数只剩标签"都不会留下 undefined
  // 字样——字段缺失时 React 直接渲染成空。所以要正面断言内容在。
  await enterDemo(page);
  await page.goto("/frameworks");
  await page.waitForTimeout(600);
  const text = await page.evaluate(() => document.body.innerText);
  expect(text, "框架列表没有框架名").toContain("NIST");
  expect(text, "条目数只剩标签，没有数字").toMatch(/\d+\s*(条目数|items)/i);
});
