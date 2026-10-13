import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function mockSession(page: Page, role = "contributor") {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/auth/me", (route) => route.fulfill({
    json: { id: 7, email: `${role}@example.com`, name: "User", role },
  }));
}

const asset = {
  id: 4, name: "VaultKeeper", category: "pam", vendor: "VaultKeeper", environment: "prod",
  owner_user_id: 7, scope_note: "Privileged accounts", status: "active", created_at: "2026-09-10T00:00:00Z",
};

test("tech asset registry expands to show supported controls", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/tech-assets", (route) => route.fulfill({ json: [asset] }));
  await page.route("**/api/tech-assets/4/controls", (route) => route.fulfill({ json: [{
    id: 10, control_id: 12, tech_asset_id: 4, description: "Session review", how_enforced: "automated",
    status: "implemented", na_justification: null, owner_user_id: 7, last_verified_at: null,
    control_code: "AC-01", control_title: "Privileged access review",
  }] }));
  await page.goto("/tech-assets");
  await expect(page.getByRole("cell", { name: "VaultKeeper", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Show controls" }).click();
  await expect(page.getByText("AC-01")).toBeVisible();
  await expect(page.getByText("Privileged access review")).toBeVisible();
});

test("evidence library combines status and owner filters", async ({ page }) => {
  await mockSession(page, "viewer");
  const requested: string[] = [];
  await page.route("**/api/evidence**", async (route) => {
    if (new URL(route.request().url()).pathname !== "/api/evidence") return route.fallback();
    requested.push(route.request().url());
    await route.fulfill({ json: [{
      id: 9, evidence_type_id: 2, control_id: 12, tech_asset_id: 4, title: "Quarterly PAM sample",
      owner_user_id: 7, location_hint: "VaultKeeper", last_collected_at: "2026-01-01T00:00:00Z",
      valid_until: "2026-03-31T00:00:00Z", file_path: null, status: "expired", intent_status: "collected",
      display_status: "expired", created_at: "2026-01-01T00:00:00Z", evidence_type_name: "PAM sample",
      control_code: "AC-01", control_title: "Privileged access review", tech_asset_name: "VaultKeeper",
    }] });
  });
  await page.goto("/evidence");
  await expect(page.getByText("Quarterly PAM sample")).toBeVisible();
  await page.getByLabel("Display status").selectOption("expired");
  await page.getByLabel("Owner user ID").fill("7");
  await expect.poll(() => requested.at(-1) ?? "").toContain("status=expired");
  await expect.poll(() => requested.at(-1) ?? "").toContain("owner_user_id=7");
  await expect(page.getByRole("table").getByText("Expired", { exact: true })).toBeVisible();
});

test("overview shows the live expired evidence count", async ({ page }) => {
  await mockSession(page, "viewer");
  await page.route("**/api/settings/usage", (route) => route.fulfill({ json: { month_to_date_cost: 0, budget: null, by_task: [] } }));
  await page.route("**/api/evidence/stats", (route) => route.fulfill({ json: { expired: 3 } }));
  await page.goto("/");
  await expect(page.getByText("Expired evidence", { exact: true })).toBeVisible();
  await expect(page.getByText("3", { exact: true })).toBeVisible();
  await expect(page.getByText("Needs attention", { exact: true })).toBeVisible();
});

// ── 冷启动空状态 ── OQ-24 ──────────────────────────────────────────
//
// 新部署时控制点库和证据类型都是空的，"新增证据"的保存按钮因而永久置灰
// （disabled 条件里有 !form.control_id 和 !form.evidence_type_id），
// 界面上不给任何解释。每个新租户上线第一天必撞。
//
// 验的是**它说出缺什么**，不是"按钮是灰的"——按钮灰着是对的，
// 缺的是那句话。

test("empty control library explains what to create first instead of a dead grey button", async ({ page }) => {
  await mockSession(page, "contributor");
  await page.route("**/api/evidence**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/evidence-types", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/controls**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/tech-assets", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/users", (route) => route.fulfill({ json: [] }));

  await page.goto("/evidence");
  await page.getByRole("button", { name: "Add", exact: true }).click();

  const notice = page.getByRole("note");
  await expect(notice).toBeVisible();
  // 两个前置都空，两个都要点名——只说一个会让人补完还是存不了
  await expect(notice).toContainText("control");
  await expect(notice).toContainText("evidence type");
  // 并且给得出去处
  await expect(notice.getByRole("link", { name: /control/i })).toBeVisible();
});

test("a satisfied prerequisite is not listed as missing", async ({ page }) => {
  await mockSession(page, "contributor");
  await page.route("**/api/evidence**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/evidence-types", (route) => route.fulfill({
    json: [{ id: 1, name: "Screenshot", default_validity_days: 90 }],
  }));
  await page.route("**/api/controls**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/tech-assets", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/users", (route) => route.fulfill({ json: [] }));

  await page.goto("/evidence");
  await page.getByRole("button", { name: "Add", exact: true }).click();

  const notice = page.getByRole("note");
  await expect(notice).toContainText("control");
  await expect(notice).not.toContainText("evidence type");
});

// ── 证据的时间在东八区被来回换算 ──────────────────────────────────
//
// 编辑框原来用 `iso.slice(0, 16)` 填 `datetime-local`：UTC 的钟点被当成本地钟点，
// 保存时再按本地解析。东八区每保存一次，采集时间和有效期都往前挪 8 小时——
// 哪怕只改了标题，那两个框根本没碰过。

test.describe("evidence times in a UTC+8 browser", () => {
  test.use({ timezoneId: "Asia/Shanghai" });

  // 日期筛选原来拼成 UTC 的 00:00 / 23:59:59：「某日之前」多算进 8 小时，日期框回显也会差一天。
  test("the due-date filter covers the local day", async ({ page }) => {
    await mockSession(page, "viewer");
    const requested: string[] = [];
    await page.route("**/api/evidence**", (route) => {
      const url = new URL(route.request().url());
      if (url.pathname !== "/api/evidence") return route.fallback();
      requested.push(url.search);
      return route.fulfill({ json: [] });
    });
    await page.goto("/evidence");
    await page.getByLabel("Due before").fill("2026-10-01");
    await expect.poll(() => requested.at(-1) ?? "").toContain("due_before=2026-10-01T15%3A59%3A59.999Z");
    await page.getByLabel("Valid after").fill("2026-09-01");
    await expect.poll(() => requested.at(-1) ?? "").toContain("due_after=2026-08-31T16%3A00%3A00.000Z");
    await expect(page.getByLabel("Due before")).toHaveValue("2026-10-01");
    await expect(page.getByLabel("Valid after")).toHaveValue("2026-09-01");
  });

  test("editing only the title sends the times back unchanged", async ({ page }) => {
    await mockSession(page, "contributor");
    const item = {
      id: 9, evidence_type_id: 2, control_id: 12, tech_asset_id: null, title: "Quarterly PAM sample",
      owner_user_id: null, location_hint: "", last_collected_at: "2026-09-01T02:30:15Z",
      valid_until: "2026-12-31T16:00:00Z", file_path: null, status: "collected", intent_status: "collected",
      display_status: "valid", created_at: "2026-09-01T00:00:00Z", evidence_type_name: "PAM sample",
      control_code: "AC-01", control_title: "Privileged access review", tech_asset_name: null,
    };
    let sent: Record<string, unknown> | null = null;
    await page.route("**/api/evidence**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/evidence/9" && route.request().method() === "PATCH") {
        sent = route.request().postDataJSON();
        return route.fulfill({ json: item });
      }
      if (path === "/api/evidence") return route.fulfill({ json: [item] });
      return route.fallback();
    });
    await page.route("**/api/evidence-types", (route) => route.fulfill({
      json: [{ id: 2, name_zh: "PAM 样本", name_en: "PAM sample", format: "", cadence: "", typical_source: "", description: "" }],
    }));
    await page.route("**/api/controls**", (route) => route.fulfill({ json: [{ id: 12, code: "AC-01", title: "Privileged access review" }] }));
    await page.route("**/api/tech-assets", (route) => route.fulfill({ json: [] }));

    await page.goto("/evidence");
    await page.getByRole("button", { name: "Edit", exact: true }).click();

    // 框里是本地钟点：02:30Z 是北京时间 10:30，16:00Z 是次日 00:00。
    await expect(page.getByLabel("Last collected")).toHaveValue("2026-09-01T10:30");
    await expect(page.getByLabel("Valid until")).toHaveValue("2027-01-01T00:00");

    await page.getByLabel("Title").fill("Quarterly PAM sample (Q3)");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => sent).not.toBeNull();
    expect(sent).toMatchObject({
      title: "Quarterly PAM sample (Q3)",
      last_collected_at: "2026-09-01T02:30:15Z",
      valid_until: "2026-12-31T16:00:00Z",
    });
  });

  test("a changed time is read as local time", async ({ page }) => {
    await mockSession(page, "contributor");
    const item = {
      id: 9, evidence_type_id: 2, control_id: 12, tech_asset_id: null, title: "Quarterly PAM sample",
      owner_user_id: null, location_hint: "", last_collected_at: "2026-09-01T02:30:00Z",
      valid_until: null, file_path: null, status: "collected", intent_status: "collected",
      display_status: "collected", created_at: "2026-09-01T00:00:00Z", evidence_type_name: "PAM sample",
      control_code: "AC-01", control_title: "Privileged access review", tech_asset_name: null,
    };
    let sent: Record<string, unknown> | null = null;
    await page.route("**/api/evidence**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/evidence/9" && route.request().method() === "PATCH") {
        sent = route.request().postDataJSON();
        return route.fulfill({ json: item });
      }
      if (path === "/api/evidence") return route.fulfill({ json: [item] });
      return route.fallback();
    });
    await page.route("**/api/evidence-types", (route) => route.fulfill({
      json: [{ id: 2, name_zh: "PAM 样本", name_en: "PAM sample", format: "", cadence: "", typical_source: "", description: "" }],
    }));
    await page.route("**/api/controls**", (route) => route.fulfill({ json: [{ id: 12, code: "AC-01", title: "Privileged access review" }] }));
    await page.route("**/api/tech-assets", (route) => route.fulfill({ json: [] }));

    await page.goto("/evidence");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page.getByLabel("Valid until").fill("2027-01-01T08:00");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => sent).not.toBeNull();
    expect(sent).toMatchObject({ last_collected_at: "2026-09-01T02:30:00Z", valid_until: "2027-01-01T00:00:00.000Z" });
  });
});

// 删除证据类型是真删，不能撤回；原来点一下就删了。
test("deleting an evidence type asks first", async ({ page }) => {
  await mockSession(page, "admin");
  let deletes = 0;
  await page.route("**/api/evidence-types**", (route) => {
    if (route.request().method() === "DELETE") {
      deletes += 1;
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fulfill({ json: [{
      id: 2, name_zh: "PAM 样本", name_en: "PAM sample", format: "", cadence: "quarterly",
      typical_source: "", description: "",
    }] });
  });
  await page.goto("/settings/evidence-types");
  const row = page.getByRole("row", { name: /PAM sample/ });

  page.once("dialog", (dialog) => void dialog.dismiss());
  await row.getByRole("button", { name: "Delete" }).click();
  expect(deletes).toBe(0);

  page.once("dialog", (dialog) => {
    expect(dialog.message()).toContain("PAM sample");
    void dialog.accept();
  });
  await row.getByRole("button", { name: "Delete" }).click();
  await expect.poll(() => deletes).toBe(1);
});
