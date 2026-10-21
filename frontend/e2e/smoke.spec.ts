import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const ADMIN = { email: "admin@example.com", password: "pw123456" };
const SECRET = "sk-e2e-supersecret-value";
const PROVIDER_NAME = `e2e-${Date.now()}`;
// 后端直连地址：`make e2e` 把整套栈开在另一组端口上，别写死 8000。
const API = process.env.E2E_API_BASE ?? "http://localhost:8000";

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Compliance & Governance Cockpit" })).toBeVisible();
}

test("登录页不嵌第三方 iframe", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(0);
});

test("管理员能登录并看到总览", async ({ page }) => {
  await signIn(page);
  await expect(page.getByText("Run Pipeline")).toHaveCount(0);
  await expect(page.getByText("Active DLP Redaction")).toHaveCount(0);
});

// 界面默认英文；左下角 EN/中 可切到中文再切回，选择存在 localStorage，刷新后保持。
test("默认英文，可切换到中文再切回", async ({ page }) => {
  await signIn(page);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await page.goto("/documents");
  await expect(page.getByText("Regulatory and internal policy repository")).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Actions" })).toBeVisible();
  await page.goto("/settings/providers");
  await expect(page.getByRole("columnheader", { name: "API key" })).toBeVisible();
  await expect(page.getByText("Assign each inference task to a dedicated AI provider")).toBeVisible();

  await page.goto("/documents");
  await page.getByRole("button", { name: "切换到中文" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.getByText("监管与内部制度库")).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "操作" })).toBeVisible();

  await page.getByRole("button", { name: "Switch to English" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByText("Regulatory and internal policy repository")).toBeVisible();
  await expect(page.getByText("监管与内部制度库")).toHaveCount(0);
});

test("新建的 provider 只显示掩码后的 key", async ({ page }) => {
  await signIn(page);
  await page.goto("/settings/providers");

  await page.getByPlaceholder("Name").fill(PROVIDER_NAME);
  await page.getByPlaceholder("API key").fill(SECRET);
  await page.getByRole("button", { name: "Save" }).first().click();

  // 名字在路由下拉的 option 里也会出现，且历次冒烟会在库里留下多行，
  // 所以一律锁定到本次创建的那一行。
  const row = page.getByRole("row", { name: new RegExp(PROVIDER_NAME) });
  await expect(row).toBeVisible();
  await expect(page.getByText(SECRET)).toHaveCount(0);
  await expect(row.getByRole("code")).toHaveText("…alue");

  // 停用与删除都在这一行上。删除走两步，误点一下不会真的没了。
  await row.getByRole("button", { name: "Disable" }).click();
  await expect(row.getByRole("button", { name: "Enable" })).toBeVisible();
  await row.getByRole("button", { name: "Delete" }).click();
  await row.getByRole("button", { name: "Delete for good" }).click();
  await expect(row).toHaveCount(0);
  // 这张表此前只进不出：每跑一次冒烟就留一行。现在用例自己收尾。
});

test("脱敏预览显示实际会发出去的内容", async ({ page }) => {
  await signIn(page);
  await page.goto("/settings/redaction");

  await page.getByLabel("Send preview input").fill("跳板机 10.20.30.40 需季度复核");
  await page.getByRole("button", { name: "Preview" }).click();

  const preview = page.locator("pre");
  await expect(preview).toContainText("[[IP_1]]");
  await expect(preview).not.toContainText("10.20.30.40");
});

test("阈值顺序错误会被拒绝", async ({ page }) => {
  await signIn(page);
  // 扣住读取，把"服务端的值落在用户输入之后"这个时序固定住。它曾经只是偶发：
  // 值恰好落在两次填写之间，就把第一个框还原了，校验于是不再触发。
  // 现在的约定是数据到之前根本不能输入。
  let release = () => {};
  const held = new Promise<void>((done) => (release = done));
  await page.route("**/api/settings/thresholds", async (route) => {
    if (route.request().method() === "GET") await held;
    await route.continue();
  });
  await page.goto("/settings/thresholds");
  await expect(page.getByLabel("Auto-accept threshold (scores above this can be batch accepted)")).toBeDisabled();
  release();

  await page.getByLabel("Auto-accept threshold (scores above this can be batch accepted)").fill("0.3");
  await page.getByLabel("Force manual threshold (scores below this require manual confirmation)").fill("0.8");
  await page.getByRole("button", { name: "Save" }).click();

  await expect(page.getByRole("alert")).toContainText("must be higher than");
});

test("配置变更留下了审计日志且不含明文密钥", async ({ page }) => {
  await signIn(page);
  await page.goto("/settings/audit-log");

  await expect(page.getByText("provider.create").first()).toBeVisible();
  await expect(page.getByText(SECRET)).toHaveCount(0);
});

test("用户列表加载失败时显示错误并允许重试", async ({ page }) => {
  await signIn(page);
  await page.route("**/api/users", (route) => route.fulfill({ status: 500, json: { message: "user service unavailable" } }));
  await page.goto("/settings/users");

  await expect(page.getByRole("alert")).toContainText("user service unavailable");
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
});

test("GRC Lead 看不到也进不去 AI 配置页", async ({ page, request }) => {
  await signIn(page);
  await page.goto("/settings/users");

  const lead = `lead-${Date.now()}@example.com`;
  await page.getByPlaceholder("Email").fill(lead);
  await page.getByPlaceholder("Name").fill("Lead");
  await page.getByLabel("New user role").selectOption("grc_lead");
  await page.getByPlaceholder("Initial password (at least 8 chars)").fill("pw123456");
  await page.getByRole("button", { name: "Save" }).click();
  // exact 必需：启用状态那格的 aria-label 里也带着邮箱
  await expect(page.getByRole("cell", { name: lead, exact: true })).toBeVisible();

  // 换成 GRC Lead 登录：菜单里不该出现 AI 配置入口
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

  await page.getByLabel("Email").fill(lead);
  await page.getByLabel("Password").fill("pw123456");
  await page.getByRole("button", { name: "Sign in" }).click();
  // 登录成功后停在退出前的 URL，不会回首页——用"退出按钮出现"当已登录的标志。
  // 不等这一步就 goto，会赶在 token 写入之前。
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await page.goto("/settings");
  await expect(page.getByRole("link", { name: "AI Provider" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Audit log" })).toBeVisible();

  // 前端只是藏入口，真正的拦截在后端——直接打 API 必须 403
  const token = await page.evaluate(() => localStorage.getItem("grc.token"));
  const denied = await request.get(`${API}/api/settings/providers`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(denied.status()).toBe(403);
});

test("侧栏导航再长，退出按钮也留在视口里", async ({ page }) => {
  await signIn(page);

  // 导航项每加一个就把底部的退出/主题切换往下顶。侧栏本身必须能滚，
  // 否则 720 高的屏幕上这些控件直接够不着——2026-09-11 图谱项就顶出去过一次。
  const box = await page.getByRole("button", { name: "Sign out" }).boundingBox();
  const viewport = page.viewportSize()!;
  expect(box).not.toBeNull();
  expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height);
});

test("顶部心跳反映真实健康状态，而不是常亮的绿灯", async ({ page }) => {
  // 这个指示器原本是写死的 <div>：库挂了照样显示"系统运行正常"，
  // 对着一屋子审计人员撒谎。所以要验的不是"它显示绿"，而是
  // **它会因为后端的回答而改变**。

  // 先确认它真的去问了后端
  const asked = page.waitForRequest((r) => r.url().includes("/api/health"));
  await signIn(page);
  await asked;
  await expect(page.getByText("All Systems Operational")).toBeVisible();

  // 后端报 503 降级时，指示器必须跟着变
  await page.route("**/api/health", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ status: "degraded", database: "down", version: "0.1.0" }),
    }),
  );
  await page.reload();
  await expect(page.getByText("System degraded")).toBeVisible();
  await expect(page.getByText("All Systems Operational")).toHaveCount(0);
});

test("窄屏下侧边栏收进抽屉，内容区不被挤掉也不横向溢出", async ({ page }) => {
  // 改造前：侧栏固定 width:256 且 flexShrink:0，375px 下主内容只剩几十像素，
  // 整页横向溢出约 711px。全站 @media 命中数当时是 0——响应式从未实现。
  await page.setViewportSize({ width: 375, height: 667 });
  await signIn(page);

  // 窄屏图标轨隐藏，常用四页放在底部标签栏，其余页面收进抽屉。
  // 抽屉默认收起：抽屉里的导航链接不占位
  const documents = page.getByRole("complementary").getByRole("link", { name: "Documents", exact: true });
  await expect(documents).not.toBeInViewport();

  // 最要紧的一条：不许横向溢出
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  // 不溢出还不够：内容区曾被宽屏的三列网格挤进 64px 的第一列，照样不溢出
  const main = await page.getByRole("main").boundingBox();
  expect(main?.width).toBeGreaterThan(360);

  // 打得开、进得去
  await page.getByRole("navigation").getByRole("button", { name: "Menu" }).click();
  await expect(documents).toBeInViewport();
  await documents.click();
  await expect(page).toHaveURL(/\/documents$/);
  // 跳转后抽屉自动收起，否则挡着刚打开的页面
  await expect(documents).not.toBeInViewport();
});

test("宽屏收起过侧边面板，到窄屏抽屉照样打得开", async ({ page }) => {
  // 收起偏好存在 localStorage 里，窄屏也带着 ws-shell--collapsed；那条 visibility:hidden
  // 的选择器比窄屏抽屉的规则更具体，抽屉滑进来了却看不见。
  await page.addInitScript(() => localStorage.setItem("grc.ws.panel", "closed"));
  await page.setViewportSize({ width: 375, height: 667 });
  await signIn(page);

  await page.getByRole("navigation").getByRole("button", { name: "Menu" }).click();
  await expect(page.getByRole("complementary").getByRole("link", { name: "Documents", exact: true })).toBeVisible();
});

test("宽屏不受抽屉改造影响：侧边栏常驻，没有汉堡按钮", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page);
  await expect(page.getByRole("navigation")).toBeInViewport();
  await expect(page.getByRole("button", { name: "Open navigation" })).toBeHidden();
});

test("只读账号直接敲设置页 URL 会被弹回首页，而不是看到管理员页面的空壳", async ({ page, request }) => {
  // 后端对这三个接口都返 403，所以本来就没有数据泄露；问题是前端照常渲染
  // 管理员页面的骨架（表单、按钮、报错），让只读用户以为自己该有这些权限。
  // `SettingsLayout` 只是把标签藏起来，`<Outlet />` 仍然渲染子路由。
  const viewer = `viewer-${Date.now()}@example.com`;
  const admin = await request.post(`${API}/api/auth/login`, { data: ADMIN });
  const token = (await admin.json()).access_token;
  await request.post(`${API}/api/users`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { email: viewer, name: "Viewer", password: "pw123456", role: "viewer" },
  });

  await page.goto("/");
  await page.getByLabel("Email").fill(viewer);
  await page.getByLabel("Password").fill("pw123456");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Compliance & Governance Cockpit" })).toBeVisible();

  for (const path of ["/settings/users", "/settings/providers", "/settings/audit-log"]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/$/);
  }
});

test("证据类型有地方维护——顺着前置提示点进去就能把冷启动解开", async ({ page }) => {
  // 后端 POST /api/evidence-types 一直都在，但前端没有任何界面能建它，
  // 而证据登记的保存按钮必须有 evidence_type_id——冷启动因此是死的。
  // 这条走的就是用户的真实路径：看到引导 → 点进去 → 建成 → 回来能用。
  await signIn(page);

  await page.goto("/evidence");
  await page.getByRole("button", { name: "Add", exact: true }).click();

  // 引导必须指向**真的能建它**的页面。先前指的是 /settings，那里没有这个标签。
  const link = page.getByRole("note").getByRole("link", { name: "an evidence type" });
  await expect(link).toHaveAttribute("href", "/settings/evidence-types");
  await link.click();
  await expect(page).toHaveURL(/\/settings\/evidence-types$/);

  const name = `E2E evidence type ${Date.now()}`;
  await page.getByLabel("Name (English)").fill(name);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("cell", { name })).toBeVisible();

  // 回到证据登记：这条前置已满足，就不该再被列出来
  await page.goto("/evidence");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("note").getByRole("link", { name: "an evidence type" })).toHaveCount(0);
});
