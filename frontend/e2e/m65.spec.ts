import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const relationGraph = {
  nodes: [
    { key: "control:1", kind: "control", code: "C-0001", title: "CAB approval", group: "document:3", group_extra: [], functions: ["GV"], pending_edges: 0, is_gap: false },
    { key: "control:2", kind: "control", code: "C-0002", title: "Rollback plan", group: "document:3", group_extra: [], functions: ["PR"], pending_edges: 0, is_gap: false },
    { key: "control:3", kind: "control", code: "C-0003", title: "Incident logging", group: "document:4", group_extra: [], functions: [], pending_edges: 0, is_gap: false },
  ],
  edges: [
    { key: "relation:11", source: "control:1", target: "control:2", kind: "depends_on", status: "confirmed", confidence: 0.9, rationale: "approval precedes rollback", proposal_id: null },
    { key: "relation:12", source: "control:2", target: "control:3", kind: "duplicates", status: "confirmed", confidence: 0.95, rationale: "same requirement", proposal_id: null },
    { key: "conflict:control:1:control:2:confirmed", source: "control:1", target: "control:2",
      kind: "conflicts_with", status: "confirmed", confidence: null, rationale: "",
      proposal_id: null, conflict_count: 2 },
  ],
  groups: [
    { key: "document:3", kind: "document", label: "Change Management Procedure" },
    { key: "document:4", kind: "document", label: "Incident Management Guideline" },
  ],
  stats: { nodes: 3, edges: 3, pending_edges: 0, truncated: false },
};

export async function mockGraph(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { id: 1, email: "lead@example.com", name: "Lead", role: "grc_lead" } });
    if (url.pathname === "/api/frameworks") return route.fulfill({ json: [{ id: 7, name_zh: "网络安全框架", name_en: "Cybersecurity Framework", version: "2.0" }] });
    if (url.pathname === "/api/documents") return route.fulfill({ json: [{ id: 3, title: "Change Management Procedure" }, { id: 4, title: "Incident Management Guideline" }] });
    if (url.pathname === "/api/controls") return route.fulfill({ json: [{ id: 1, code: "C-0001", title: "CAB approval" }, { id: 2, code: "C-0002", title: "Rollback plan" }, { id: 3, code: "C-0003", title: "Incident logging" }] });
    if (url.pathname === "/api/controls/1") return route.fulfill({ json: {
      id: 1, code: "C-0001", title: "CAB approval", statement: "Changes must be approved by the CAB.",
      category: null, owner_user_id: null, status: "active", created_at: "2026-09-01T00:00:00Z",
      sources: [{ clause_id: 91, document_id: 3, document_title: "Change Management Procedure", citation_label: "4.2", heading_path: "Change › Approval", relation: "defines" }],
      relations: [], mappings: [], implementations: [], evidence: [],
    } });
    if (url.pathname === "/api/clauses/91") return route.fulfill({ json: {
      id: 91, document_id: 3, document_title: "Change Management Procedure", number: "4.2",
      heading: "Approval", heading_path: "Change › Approval", citation_label: "4.2",
      text: "All normal changes are approved by the CAB before implementation.", level: 2, page_ref: 7,
    } });
    if (url.pathname === "/api/graph/relations") {
      if (url.searchParams.get("include_pending") === "true") {
        return route.fulfill({ json: {
          ...relationGraph,
          nodes: relationGraph.nodes.map((node) => node.key === "control:3" ? { ...node, pending_edges: 1 } : node),
          edges: [
            ...relationGraph.edges,
            { key: "proposal:31", source: "control:1", target: "control:3", kind: "depends_on", status: "pending", confidence: 0.8, rationale: "model said so", proposal_id: 31 },
          ],
          stats: { nodes: 3, edges: 4, pending_edges: 1, truncated: false },
        } });
      }
      return route.fulfill({ json: relationGraph });
    }
    if (url.pathname === "/api/graph/mappings") return route.fulfill({ json: {
      nodes: [
        { key: "control:1", kind: "control", code: "C-0001", title: "CAB approval", group: null, group_extra: [], functions: [], pending_edges: 0, is_gap: false },
        { key: "item:100", kind: "framework_item", code: "GV.PO-01", title: "Policy", group: null, group_extra: [], functions: [], pending_edges: 0, is_gap: false },
        { key: "item:200", kind: "framework_item", code: "PR.AA-01", title: "Access", group: null, group_extra: [], functions: [], pending_edges: 0, is_gap: true },
      ],
      edges: [
        { key: "mapping:5", source: "control:1", target: "item:100", kind: "full", status: "confirmed", confidence: 0.9, rationale: "covers it", proposal_id: null },
      ],
      groups: [],
      stats: { nodes: 3, edges: 1, pending_edges: 0, truncated: false },
    } });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url.pathname}` } });
  });
}

test("the relation graph renders nodes and edges grouped by document", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  await expect(page.getByRole("heading", { name: "Graph" })).toBeVisible();
  await expect(page.locator("[data-node-key]")).toHaveCount(3);
  await expect(page.locator("[data-edge-key]")).toHaveCount(3);
  await expect(page.locator('[data-edge-key="relation:11"]')).toHaveAttribute("data-kind", "depends_on");
  await expect(page.locator('[data-edge-key="relation:11"]')).toHaveAttribute("data-status", "confirmed");
  await expect(page.getByText("3 nodes · 3 edges")).toBeVisible();
  await expect(page.getByRole("list").getByText("Change Management Procedure")).toBeVisible();
});

test("nodes in the same document share a lane", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  const first = await page.locator('[data-node-key="control:1"]').getAttribute("transform");
  const second = await page.locator('[data-node-key="control:2"]').getAttribute("transform");
  const third = await page.locator('[data-node-key="control:3"]').getAttribute("transform");
  const x = (value: string | null) => value!.match(/translate\((-?\d+(?:\.\d+)?)/)![1];
  expect(x(first)).toBe(x(second));
  expect(x(first)).not.toBe(x(third));
});

test("grouping by function separates mapped controls from unmapped ones", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");
  await page.getByRole("button", { name: "By function" }).click();

  const x = async (key: string) => {
    const transform = await page.locator(`[data-node-key="${key}"]`).getAttribute("transform");
    return transform!.match(/translate\((-?\d+(?:\.\d+)?)/)![1];
  };
  // GV / PR / 未覆盖各一条泳道，三个节点两两不同列。
  expect(new Set([await x("control:1"), await x("control:2"), await x("control:3")]).size).toBe(3);
});

test("the force layout lands on the same coordinates every time", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  await page.getByRole("button", { name: "Force" }).click();
  const first = await page.locator("[data-node-key]").evaluateAll((nodes) =>
    nodes.map((node) => `${node.getAttribute("data-node-key")}@${node.getAttribute("transform")}`),
  );

  await page.getByRole("button", { name: "By document" }).click();
  await page.getByRole("button", { name: "Force" }).click();
  const second = await page.locator("[data-node-key]").evaluateAll((nodes) =>
    nodes.map((node) => `${node.getAttribute("data-node-key")}@${node.getAttribute("transform")}`),
  );

  expect(second).toEqual(first);

  await page.reload();
  await page.getByRole("button", { name: "Force" }).click();
  const third = await page.locator("[data-node-key]").evaluateAll((nodes) =>
    nodes.map((node) => `${node.getAttribute("data-node-key")}@${node.getAttribute("transform")}`),
  );
  expect(third).toEqual(first);
});

test("clicking a node opens a drawer with the source clause text", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  await page.locator('[data-node-key="control:1"]').click();

  const drawer = page.getByRole("complementary", { name: "Details" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText("C-0001")).toBeVisible();
  await expect(drawer.getByText("Changes must be approved by the CAB.")).toBeVisible();
  await expect(drawer.getByText("4.2")).toBeVisible();
  await expect(
    drawer.getByText("All normal changes are approved by the CAB before implementation."),
  ).toBeVisible();
  await expect(drawer.getByRole("link", { name: "Open in document" })).toHaveAttribute(
    "href",
    "/documents/3#clause-91",
  );
});

test("clicking an edge shows the model's rationale", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  // relation:11 与冲突边共用端点，hitbox 重叠。点一条不重叠的边才能证明真实点击。
  await page.locator('[data-edge-key="relation:12"]').click();

  const drawer = page.getByRole("complementary", { name: "Details" });
  await expect(drawer.getByText("same requirement")).toBeVisible();
  await expect(drawer.getByText("0.95")).toBeVisible();
});

test("focus and hop count travel to the API", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  const requested = page.waitForRequest((request) => {
    if (!request.url().includes("/api/graph/relations?")) return false;
    const url = new URL(request.url());
    return url.searchParams.get("focus") === "control:1" && url.searchParams.get("hops") === "2";
  });
  await page.getByLabel("Focus").selectOption("control:1");
  await page.getByLabel("Hops").selectOption("2");
  const url = new URL((await requested).url());
  expect(url.searchParams.get("hops")).toBe("2");
});

test("pending edges are dashed and counted apart from confirmed ones", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  await page.getByLabel("Show pending proposals").check();

  await expect(page.locator('[data-edge-key="proposal:31"]')).toHaveAttribute("data-status", "pending");
  await expect(page.locator('[data-edge-key="proposal:31"]')).toHaveAttribute("stroke-dasharray", "5 4");
  await expect(page.getByText("1 pending")).toBeVisible();
});

test("only-unconfirmed hides the confirmed edges without another request", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");
  await page.getByLabel("Show pending proposals").check();
  await expect(page.locator("[data-edge-key]")).toHaveCount(4);

  await page.getByLabel("Only unconfirmed").check();

  await expect(page.locator("[data-edge-key]")).toHaveCount(1);
  await expect(page.locator('[data-edge-key="proposal:31"]')).toBeVisible();
});

test("conflicts-only narrows the requested relation types", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  const requested = page.waitForRequest((request) => request.url().includes("types=conflicts_with"));
  await page.getByLabel("Only conflicts").check();
  await requested;
});

test("the mapping view puts controls and framework items in two columns and flags gaps", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  await page.getByRole("button", { name: "Framework mapping" }).click();

  await expect(page.locator('[data-node-key="item:200"]')).toHaveAttribute("data-gap", "true");
  await expect(page.locator('[data-node-key="item:100"]')).toHaveAttribute("data-gap", "false");
  const x = async (key: string) => {
    const transform = await page.locator(`[data-node-key="${key}"]`).getAttribute("transform");
    return Number(transform!.match(/translate\((-?\d+(?:\.\d+)?)/)![1]);
  };
  expect(await x("control:1")).toBeLessThan(await x("item:100"));
  expect(await x("item:100")).toBe(await x("item:200"));
  await expect(page.getByText("1 gap")).toBeVisible();
});

test("the layout buttons are gone in the mapping view", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");
  await page.getByRole("button", { name: "Framework mapping" }).click();

  await expect(page.getByRole("button", { name: "Force" })).toHaveCount(0);
});

test("panorama thins labels down to the busiest nodes", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  // 三个节点里 control:2 度数最高（两条边），标签上限设为 1 时只剩它带标签。
  await page.getByLabel("Thin labels").check();

  await expect(page.locator("[data-node-label]")).toHaveCount(1);
  await expect(page.locator("[data-node-label]")).toHaveText("C-0002");
});

test("exporting produces an svg file with literal colours", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export SVG" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("graph.svg");

  const stream = await file.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(chunk as Buffer);
  const content = Buffer.concat(chunks).toString("utf8");
  expect(content).toContain("<svg");
  expect(content).not.toContain("var(--");
});

test("exporting png triggers a download", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export PNG" }).click();
  expect((await download).suggestedFilename()).toBe("graph.png");
});

// ---------------------------------------------------------------------------
// 大图夹具。三节点夹具撑不开力导向，也撑不出超高的画布——
// 两个真实数据上暴露的缺陷都只有在节点够多时才出得来。
// ---------------------------------------------------------------------------

const BIG_CONTROLS = 120;
const BIG_ITEMS = 800;

async function mockBigGraph(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  const controls = Array.from({ length: BIG_CONTROLS }, (_, index) => ({
    key: `control:${index + 1}`,
    kind: "control",
    code: `C-${String(index + 1).padStart(4, "0")}`,
    title: `Control ${index + 1}`,
    group: `document:${(index % 4) + 1}`,
    group_extra: [],
    functions: [],
    pending_edges: 0,
    is_gap: false,
  }));
  const relationEdges = Array.from({ length: 40 }, (_, index) => ({
    key: `relation:${index + 1}`,
    source: `control:${index + 1}`,
    target: `control:${index + 41}`,
    kind: "depends_on",
    status: "confirmed",
    confidence: 0.9,
    rationale: "r",
    proposal_id: null,
  }));
  const items = Array.from({ length: BIG_ITEMS }, (_, index) => ({
    key: `item:${index + 1}`,
    kind: "framework_item",
    code: `GV.PO-${String(index + 1).padStart(3, "0")}`,
    title: `Item ${index + 1}`,
    group: null,
    group_extra: [],
    functions: [],
    pending_edges: 0,
    // 前三项有覆盖，其余全是差距——差距数 > 1，复数形式才测得到。
    is_gap: index >= 3,
  }));

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { id: 1, email: "lead@example.com", name: "Lead", role: "grc_lead" } });
    if (url.pathname === "/api/frameworks") return route.fulfill({ json: [{ id: 7, name_zh: "网络安全框架", name_en: "Cybersecurity Framework", version: "2.0" }] });
    if (url.pathname === "/api/documents") return route.fulfill({ json: [] });
    if (url.pathname === "/api/controls") return route.fulfill({ json: [] });
    if (url.pathname === "/api/graph/relations") return route.fulfill({ json: {
      nodes: controls,
      edges: relationEdges,
      groups: [],
      stats: { nodes: controls.length, edges: relationEdges.length, pending_edges: 0, truncated: false },
    } });
    if (url.pathname === "/api/graph/mappings") return route.fulfill({ json: {
      nodes: [controls[0], ...items],
      edges: [],
      groups: [],
      stats: { nodes: items.length + 1, edges: 0, pending_edges: 0, truncated: false },
    } });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url.pathname}` } });
  });
}

async function nodeBoxes(page: Page) {
  return page.locator("[data-node-key]").evaluateAll((nodes) =>
    nodes.map((node) => {
      const match = node.getAttribute("transform")!.match(/translate\((-?[\d.]+),(-?[\d.]+)\)/)!;
      return { x: Number(match[1]), y: Number(match[2]) };
    }),
  );
}

test("the force layout keeps every node inside the canvas", async ({ page }) => {
  await mockBigGraph(page);
  await page.goto("/graph");

  await page.getByRole("button", { name: "Force" }).click();
  await expect(page.locator("[data-node-key]")).toHaveCount(BIG_CONTROLS);

  const viewBox = (await page.locator("#graph-canvas").getAttribute("viewBox"))!.split(" ").map(Number);
  const outside = (await nodeBoxes(page)).filter(
    (point) => point.x < 0 || point.y < 0 || point.x > viewBox[2] || point.y > viewBox[3],
  );
  expect(outside).toEqual([]);
});

test("the force layout still spreads nodes apart after being fitted", async ({ page }) => {
  await mockBigGraph(page);
  await page.goto("/graph");

  await page.getByRole("button", { name: "Force" }).click();
  await expect(page.locator("[data-node-key]")).toHaveCount(BIG_CONTROLS);

  // 夹进画布不等于压成一团：横竖都得铺开到画布的一半以上。
  const points = await nodeBoxes(page);
  const spanX = Math.max(...points.map((p) => p.x)) - Math.min(...points.map((p) => p.x));
  const spanY = Math.max(...points.map((p) => p.y)) - Math.min(...points.map((p) => p.y));
  expect(spanX).toBeGreaterThan(480);
  expect(spanY).toBeGreaterThan(280);
});

test("exporting png works on a canvas too tall for a 2x bitmap", async ({ page }) => {
  await mockBigGraph(page);
  await page.goto("/graph");

  await page.getByRole("button", { name: "Framework mapping" }).click();
  await expect(page.locator('[data-kind="framework_item"]')).toHaveCount(BIG_ITEMS);
  const viewBox = (await page.locator("#graph-canvas").getAttribute("viewBox"))!.split(" ").map(Number);
  expect(viewBox[3]).toBeGreaterThan(16384); // 2 倍图会撞上浏览器的画布上限

  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export PNG" }).click();
  expect((await download).suggestedFilename()).toBe("graph.png");
});

test("the gap count reads as a plural when there is more than one gap", async ({ page }) => {
  await mockBigGraph(page);
  await page.goto("/graph");

  await page.getByRole("button", { name: "Framework mapping" }).click();
  await expect(page.getByText(`${BIG_ITEMS - 3} gaps`)).toBeVisible();
});

test("clicking a framework item shows it in the drawer instead of an empty panel", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");
  await page.getByRole("button", { name: "Framework mapping" }).click();

  await page.locator('[data-node-key="item:200"]').click();

  const drawer = page.getByRole("complementary", { name: "Details" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText("PR.AA-01")).toBeVisible();
  await expect(drawer.getByText("Access")).toBeVisible();
  await expect(drawer.getByText("Not covered")).toBeVisible();
});

test("a png export that cannot be rendered says so instead of doing nothing", async ({ page }) => {
  await mockGraph(page);
  // 逼出失败路径：让 toBlob 一律给 null，等价于画布大到浏览器开不出来。
  await page.addInitScript(() => {
    HTMLCanvasElement.prototype.toBlob = function (callback: BlobCallback) {
      callback(null);
    };
  });
  await page.goto("/graph");

  await page.getByRole("button", { name: "Export PNG" }).click();

  await expect(page.getByText("too large to export as PNG")).toBeVisible();
});

test("a conflict edge says how many conflict points it carries", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  await page.locator('[data-edge-key="conflict:control:1:control:2:confirmed"]').click();

  const drawer = page.getByRole("complementary", { name: "Details" });
  await expect(drawer.getByText("Conflicts with")).toBeVisible();
  await expect(drawer.getByText("2 conflict points")).toBeVisible();
});

test("the floating HUD provides zoom in, zoom out, and reset controls", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  const hud = page.locator(".kn-canvas-hud");
  await expect(hud).toBeVisible();
  await expect(hud.locator(".kn-hud-badge")).toHaveText("100%");

  // Zoom In
  await hud.getByRole("button", { name: "Zoom in" }).click();
  await expect(hud.locator(".kn-hud-badge")).toHaveText("125%");
  await expect(page.locator("#graph-viewport")).toHaveAttribute(
    "transform",
    /scale\(1\.25\)/,
  );

  // Reset
  await hud.getByRole("button", { name: "Reset view" }).click();
  await expect(hud.locator(".kn-hud-badge")).toHaveText("100%");

  // Zoom Out
  await hud.getByRole("button", { name: "Zoom out" }).click();
  await expect(hud.locator(".kn-hud-badge")).toHaveText("80%");
  await expect(page.locator("#graph-viewport")).toHaveAttribute(
    "transform",
    /scale\(0\.8\)/,
  );
});

test("hovering a node shows the micro-tooltip and highlights connected elements", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  const node1 = page.locator('[data-node-key="control:1"]');
  const node3 = page.locator('[data-node-key="control:3"]');
  const tooltip = page.locator(".kn-node-tooltip");

  await expect(tooltip).toHaveCount(0);

  // Hover node 1
  await node1.hover();
  await expect(tooltip).toBeVisible();
  await expect(tooltip.locator(".kn-node-tooltip-code")).toHaveText("C-0001");
  await expect(tooltip.locator(".kn-node-tooltip-title")).toHaveText("CAB approval");
  await expect(tooltip).toContainText("Control");

  // Node 3 is not connected to Node 1, so it should be dimmed
  await expect(node3).toHaveCSS("opacity", "0.22");

  // Move mouse away
  await page.mouse.move(0, 0);
  await expect(tooltip).toHaveCount(0);
});

test("nodes across all layouts render as clean dots, display labels when thin labels is unchecked, and hide labels when checked", async ({ page }) => {
  await mockGraph(page);
  await page.goto("/graph");

  // document 布局下不勾「精简标签」时，标签应当可见
  const label1 = page.locator('[data-node-key="control:1"] [data-node-label]');
  await expect(label1).toHaveCount(1);
  await expect(label1).toHaveCSS("opacity", "1");
  await expect(label1).toHaveText("C-0001");

  // 勾上「精简标签」——control:1 的标签不再渲染，被精简掉的标签 opacity 为 0
  await page.getByLabel("Thin labels").check();
  await expect(label1).toHaveCount(0);
  await expect(page.locator('[data-node-key="control:2"] [data-node-label]')).toHaveCSS("opacity", "0");

  // 切到 Force 布局，「精简标签」仍勾着 —— 标签应当保持隐藏
  await page.getByRole("button", { name: "Force" }).click();
  await expect(label1).toHaveCount(0);
  await expect(page.locator('[data-node-key="control:2"] [data-node-label]')).toHaveCSS("opacity", "0");

  // Uncheck Thin labels in Force layout -> labels appear
  await page.getByLabel("Thin labels").uncheck();
  await expect(label1).toHaveCount(1);
  await expect(label1).toHaveCSS("opacity", "1");

  // Hovering a node shows the rich micro-tooltip
  await page.locator('[data-node-key="control:1"]').hover();
  const tooltip = page.locator(".kn-node-tooltip");
  await expect(tooltip).toBeVisible();
  await expect(tooltip.locator(".kn-node-tooltip-code")).toHaveText("C-0001");
});


// 加载失败原来只是「加载中」消失、画布不出现，看起来像这张图本来就是空的。
test("a graph that fails to load says so instead of showing nothing", async ({ page }) => {
  await mockGraph(page);
  await page.route("**/api/graph/relations**", (route) => route.fulfill({
    status: 500, json: { code: "internal", message: "服务器内部错误" },
  }));
  await page.goto("/graph");
  await expect(page.getByRole("alert")).toBeVisible();
});

// 两人同时编辑同一个控制点，后保存的原来直接覆盖先保存的。现在保存时带上打开编辑
// 那一刻看到的值，库里已经变了就被拒绝，并说出是哪几栏。
test("a control edit carries what the editor saw, and a stale save is refused", async ({ page }) => {
  await mockGraph(page);
  let sent: Record<string, unknown> | null = null;
  await page.route("**/api/controls/1", (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    sent = route.request().postDataJSON();
    return route.fulfill({ status: 409, json: {
      code: "control.changed_since_loaded", message: "控制点在你打开编辑之后被修改过：title", params: { fields: ["title"] },
    } });
  });
  await page.goto("/controls/1");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByRole("textbox").nth(1).fill("Changes must be approved by the CAB and the owner.");
  await page.getByRole("button", { name: "Save", exact: true }).click();

  await expect.poll(() => sent).not.toBeNull();
  expect(sent).toMatchObject({
    expected: { title: "CAB approval", statement: "Changes must be approved by the CAB.", category: null },
  });
  await expect(page.getByRole("alert")).toContainText("Someone changed this control after you started editing (title)");
});

test("the control page names related controls by code and shows a readable status", async ({ page }) => {
  await mockGraph(page);
  await page.route("**/api/controls/1", (route) => route.request().method() !== "GET" ? route.fallback() : route.fulfill({ json: {
    id: 1, code: "C-0001", title: "CAB approval", statement: "Changes must be approved by the CAB.",
    category: null, owner_user_id: null, status: "active", created_at: "2026-09-01T00:00:00Z",
    sources: [], mappings: [], implementations: [], evidence: [],
    relations: [{ from_control_id: 2, to_control_id: 1, from_control_code: "C-0002", to_control_code: "C-0001", relation_type: "depends_on", rationale: "" }],
  } }));
  await page.goto("/controls/1");
  await expect(page.getByRole("link", { name: "C-0002", exact: true })).toBeVisible();
  await expect(page.getByText("#2")).toHaveCount(0);
  await expect(page.getByText("Status: Active")).toBeVisible();
});
