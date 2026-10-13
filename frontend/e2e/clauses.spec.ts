import { expect, test } from "@playwright/test";

test("文档页可以对条款拆分和合并", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/extraction/documents/3/coverage") {
      return route.fulfill({ json: {
        counts: { cited: 0, uncited: 0, not_completed: 1 },
        clauses: [{ clause_id: 11, citation_label: "1", status: "not_completed" }],
      } });
    }
    if (url.pathname === "/api/auth/me") {
      return route.fulfill({
        json: { id: 1, email: "c@example.com", name: "Contrib", role: "contributor" },
      });
    }
    if (url.pathname === "/api/documents/3") {
      return route.fulfill({
        json: {
          id: 3, title: "Example Visitor Access Policy", doc_type: "policy",
          status: "active", version: "1", owner: null, parse_warnings: null, supersedes_id: null,
        },
      });
    }
    if (url.pathname === "/api/documents/3/clauses") {
      return route.fulfill({
        json: [{
          id: 11, number: "1", heading: "Scope", heading_path: "Scope",
          citation_label: "1", text: "First paragraph.\n\nSecond paragraph.",
          level: 1, page_ref: null, kind: "section", children: [],
        }],
      });
    }
    return route.fulfill({ json: [] });
  });

  await page.goto("/documents/3");
  await expect(page.getByLabel("Scope")).toHaveValue("First paragraph.\n\nSecond paragraph.");
  await expect(page.getByRole("button", { name: "Split at cursor" })).toBeVisible();
  await expect(page.getByLabel("Choose a clause")).toBeVisible();
});

const clause = (id: number, number: string, heading: string, children: unknown[] = []) => ({
  id, number, heading, heading_path: heading, citation_label: number,
  text: `${heading} body.`, level: number.split(".").length, page_ref: null, kind: "section", children,
});

async function mockDocument(page: import("@playwright/test").Page, status: string) {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/extraction/documents/3/coverage") {
      return route.fulfill({ json: {
        counts: { cited: 0, uncited: 0, not_completed: 4 },
        clauses: [11, 12, 13, 14].map((id) => ({
          clause_id: id, citation_label: String(id), status: "not_completed",
        })),
      } });
    }
    if (url.pathname === "/api/auth/me") {
      return route.fulfill({
        json: { id: 1, email: "c@example.com", name: "Contrib", role: "contributor" },
      });
    }
    if (url.pathname === "/api/documents/3") {
      return route.fulfill({
        json: {
          id: 3, title: "Example Visitor Access Policy", doc_type: "policy",
          status, version: "1", owner: null, parse_warnings: null, supersedes_id: null,
        },
      });
    }
    if (url.pathname === "/api/documents/3/clauses") {
      return route.fulfill({
        json: [
          clause(11, "1", "Scope", [clause(12, "1.1", "Visitors", [clause(13, "1.1.1", "Badges")])]),
          clause(14, "2", "Roles"),
        ],
      });
    }
    return route.fulfill({ json: [] });
  });
}

// 合并会把败者的子条款挂到胜者名下；并进自己的下级条款会让树里出现环，
// 整份文档的条款树从根出发一个节点都走不到。
test("合并候选里没有当前条款自己的下级条款", async ({ page }) => {
  await mockDocument(page, "active");
  await page.goto("/documents/3");
  await expect(page.getByLabel("Scope")).toBeVisible();

  const options = page.getByLabel("Choose a clause").locator("option");
  await expect(options).toHaveText(["Choose a clause", "2 Roles"]);

  // 反方向照常可选：把误切出来的子条款并回上级。
  await page.getByRole("button", { name: "Badges" }).click();
  await expect(options).toHaveText(["Choose a clause", "1 Scope", "1.1 Visitors", "2 Roles"]);
});

test("已被新版替换的文档不再提供导入新版本", async ({ page }) => {
  await mockDocument(page, "superseded");
  await page.goto("/documents/3");
  await expect(page.getByText("Superseded")).toBeVisible();
  await expect(page.getByRole("button", { name: "Import new version" })).toHaveCount(0);
  // 旧版是历史：条款只读，不给拆、合
  await expect(page.getByRole("button", { name: "Split at cursor" })).toHaveCount(0);
  await expect(page.getByText("Scope body.")).toBeVisible();
});

// 接在一份没生效过的文档后面，新版成功时撤掉的是它，真正的现行版没人撤。
test("a document that is not in effect offers no new version either", async ({ page }) => {
  await mockDocument(page, "parse_failed");
  await page.goto("/documents/3");
  await expect(page.getByText("Parse failed")).toBeVisible();
  await expect(page.getByRole("button", { name: "Import new version" })).toHaveCount(0);
});

// 选中项的字色原来写死成白色：深色主题下看得清，浅色主题里是浅蓝底上的白字，
// 选中的条款在大纲里几乎消失。字色要跟着主题走。
for (const theme of ["light", "dark"] as const) {
  test(`the selected outline clause stays readable in the ${theme} theme`, async ({ page }) => {
    await page.addInitScript((t) => localStorage.setItem("grc.theme", t), theme);
    await mockDocument(page, "active");
    await page.goto("/documents/3");
    await page.getByRole("button", { name: "Roles" }).click();

    const selected = page.getByRole("button", { name: "Roles" });
    const expected = await page.evaluate(() => {
      const probe = document.createElement("span");
      probe.style.color = "var(--text-primary)";
      document.body.appendChild(probe);
      const color = getComputedStyle(probe).color;
      probe.remove();
      return color;
    });
    await expect(selected).toHaveCSS("color", expected);
  });
}
