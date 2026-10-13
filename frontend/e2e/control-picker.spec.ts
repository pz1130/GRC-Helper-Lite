import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/**
 * 证据登记与关系图的控制点下拉原来一次取 `limit=500`：第 501 个以后的控制点选不到
 * （KNOWN_ISSUES K11）。现在按代码 / 标题搜索，下拉只放命中的那几条。
 */

const CONTROLS = Array.from({ length: 600 }, (_, index) => ({
  id: index + 1,
  code: `C-${String(index + 1).padStart(4, "0")}`,
  title: index + 1 === 599 ? "Vendor offboarding" : `Control ${index + 1}`,
  statement: "",
}));

/** 与后端 `GET /api/controls` 同一口径：q 匹配代码/标题/陈述，limit 默认 100、上限 500。 */
function searchControls(url: URL) {
  const q = (url.searchParams.get("q") ?? "").toLowerCase();
  const limit = Number(url.searchParams.get("limit") ?? 100);
  if (limit > 500) return null;
  return CONTROLS.filter((control) =>
    !q || [control.code, control.title, control.statement].some((field) => field.toLowerCase().includes(q)),
  ).slice(0, limit);
}

async function mockApi(page: Page, extra: (url: URL, page: Page) => unknown = () => undefined) {
  await page.addInitScript(() => localStorage.setItem("grc.token", "mock-token"));
  const posted: Record<string, unknown>[] = [];
  const graphFocus: string[] = [];
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (url.pathname === "/api/auth/me") {
      return route.fulfill({ json: { id: 1, email: "admin@example.com", name: "Admin", role: "admin" } });
    }
    if (url.pathname === "/api/controls") {
      const rows = searchControls(url);
      return rows ? route.fulfill({ json: rows }) : route.fulfill({ status: 422, json: { code: "validation" } });
    }
    if (url.pathname === "/api/evidence-types") {
      return route.fulfill({ json: [{ id: 1, name_en: "Screenshot", name_zh: "", format: "", cadence: "", typical_source: "", description: "" }] });
    }
    if (url.pathname.startsWith("/api/evidence") && method !== "GET") {
      posted.push(route.request().postDataJSON());
      return route.fulfill({ json: { id: 5 } });
    }
    if (url.pathname.startsWith("/api/graph")) {
      const focus = url.searchParams.get("focus");
      if (focus) graphFocus.push(focus);
      return route.fulfill({ json: {
        nodes: [], edges: [], groups: [], stats: { nodes: 0, edges: 0, pending_edges: 0, truncated: false },
      } });
    }
    const custom = extra(url, page);
    if (custom !== undefined) return route.fulfill({ json: custom });
    return route.fulfill({ json: [] });
  });
  return { posted, graphFocus };
}

test("a control past the first 500 can be found and chosen for evidence", async ({ page }) => {
  const { posted } = await mockApi(page);
  await page.goto("/evidence");
  await page.getByRole("button", { name: "Add" }).click();
  await page.getByLabel("Title", { exact: true }).fill("Offboarding checklist");
  await page.getByLabel("Evidence type").selectOption("1");
  await page.getByRole("searchbox", { name: "Search controls" }).fill("vendor off");
  await page.getByRole("combobox", { name: "Control" }).selectOption({ label: "C-0599 · Vendor offboarding" });
  await page.getByRole("button", { name: "Save" }).click();

  await expect.poll(() => posted[0]?.control_id).toBe(599);
});

test("editing evidence shows its control even when it is not in the search results", async ({ page }) => {
  await mockApi(page, (url) => url.pathname === "/api/evidence" ? [{
    id: 5, title: "Offboarding checklist", evidence_type_id: 1, control_id: 599,
    control_code: "C-0599", control_title: "Vendor offboarding",
    tech_asset_id: null, owner_user_id: null, location_hint: "", last_collected_at: null,
    valid_until: null, file_path: null, intent_status: "planned", display_status: "planned",
  }] : undefined);
  await page.goto("/evidence");
  await page.getByRole("button", { name: "Edit" }).click();

  const control = page.getByRole("combobox", { name: "Control" });
  await expect(control).toHaveValue("599");
  await expect(control.locator("option:checked")).toHaveText("C-0599 · Vendor offboarding");
});

test("a search with no matches does not claim there are no controls", async ({ page }) => {
  await mockApi(page);
  await page.goto("/evidence");
  await page.getByRole("button", { name: "Add" }).click();
  await page.getByRole("searchbox", { name: "Search controls" }).fill("zzz-nothing");

  await expect(page.getByText("No controls match")).toBeVisible();
  // 「还没有控制点」的前置提示只看控制点是否存在，不看搜索结果
  await expect(page.getByRole("link", { name: "a control" })).toHaveCount(0);
});

test("the graph can focus on a control past the first 500", async ({ page }) => {
  const { graphFocus } = await mockApi(page);
  await page.goto("/graph");
  await page.getByRole("searchbox", { name: "Search controls" }).fill("C-0599");
  await page.getByLabel("Focus").selectOption("control:599");

  await expect.poll(() => graphFocus).toContain("control:599");
});
