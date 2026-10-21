import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function mockSession(page: Page, role = "grc_lead") {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/auth/me", (route) => route.fulfill({
    json: { id: 1, email: `${role}@example.com`, name: "User", role },
  }));
}

const file = {
  name: "framework.xlsx",
  mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  buffer: Buffer.from("mock workbook"),
};

test("invalid framework validation disables import and shows every report line", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/frameworks", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/frameworks/validate", (route) => route.fulfill({
    // 报告是 code + 参数；detail 是后端的中文兜底。原来这里模拟的是英文句子，而真实后端给的是
    // 中文——这条测试因此永远看不见英文界面冒中文。
    json: { ok: false, items: 0, report: [
      { code: "framework.missing_column", detail: "缺少必需列 title", params: { column: "title" } },
      { code: "framework.parent_missing", detail: "「PR.AA-01」的 parent_code 指向不存在的编号「MISSING」", params: { code: "PR.AA-01", parent: "MISSING" } },
    ] },
  }));
  await page.goto("/frameworks");
  await page.getByLabel("Framework file").setInputFiles(file);
  await page.getByRole("button", { name: "Validate", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Required column title is missing");
  await expect(page.getByRole("alert")).toContainText('"PR.AA-01" has a parent_code "MISSING" that does not exist');
  await expect(page.getByRole("button", { name: "Confirm import", exact: true })).toBeDisabled();
});

test("a valid framework imports and appears in the list", async ({ page }) => {
  await mockSession(page);
  let frameworks: unknown[] = [];
  await page.route("**/api/frameworks", async (route) => {
    if (new URL(route.request().url()).pathname === "/api/frameworks") return route.fulfill({ json: frameworks });
    await route.fallback();
  });
  await page.route("**/api/frameworks/validate", (route) => route.fulfill({ json: { ok: true, items: 2, report: [] } }));
  let sent = "";
  await page.route("**/api/frameworks/import", async (route) => {
    sent = route.request().postData() ?? "";
    frameworks = [{ id: 7, key: "csf", name_zh: "CSF", name_en: "CSF", version: "2.0", source: "nist.gov", item_count: 2, imported_at: "2026-09-09T00:00:00Z" }];
    return route.fulfill({ json: frameworks[0] });
  });
  await page.goto("/frameworks");
  await page.getByLabel("Framework file").setInputFiles(file);
  await page.getByLabel("Framework key").fill("csf");
  await page.getByLabel("English name").fill("CSF");
  await page.getByLabel("Version").fill("2.0");
  await page.getByLabel("Source").fill("nist.gov");
  await page.getByRole("button", { name: "Validate", exact: true }).click();
  await page.getByRole("button", { name: "Confirm import", exact: true }).click();
  await expect(page.getByRole("link", { name: "CSF", exact: true })).toBeVisible();
  // 导入表单上没有中文名一栏（制度与框架按英文设计），请求里也不带
  expect(sent).not.toContain('name="name_zh"');
});

test("coverage and gaps include the selected baseline in their requests", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/frameworks", (route) => route.fulfill({ json: [{ id: 7, key: "csf", name_zh: "CSF", name_en: "CSF", version: "2.0", source: "nist.gov", item_count: 1, imported_at: "2026-09-09T00:00:00Z" }] }));
  await page.route("**/api/frameworks/7/tree", (route) => route.fulfill({ json: [{ id: 1, parent_id: null, code: "PR", title: "Protect", description: "", level: 1, order_index: 0, attributes: null }] }));
  await page.route("**/api/frameworks/7/coverage**", (route) => route.fulfill({ json: [{ item_id: 1, code: "PR", title: "Protect", level: 1, parent_id: null, requirements: 1, covered: 0 }] }));
  await page.route("**/api/frameworks/7/gaps**", (route) => route.fulfill({ json: [{ item_id: 1, code: "PR", title: "Protect", has_supporting: true }] }));
  await page.route("**/api/frameworks/7/superseded-support**", (route) => route.fulfill({ json: [] }));
  await page.goto("/frameworks/7");
  const coverageRequest = page.waitForRequest((request) => request.url().includes("/api/frameworks/7/coverage?baseline=moderate"));
  const gapsRequest = page.waitForRequest((request) => request.url().includes("/api/frameworks/7/gaps?baseline=moderate"));
  await page.getByLabel("Baseline").selectOption("moderate");
  await coverageRequest;
  await gapsRequest;
  await expect(page.getByText("Supporting mapping exists but does not close the gap")).toBeVisible();
});

// 新版删掉某条要求、旧版被替换后，依据它的控制点与映射都还在，覆盖度照样是绿的。
// 仍算覆盖（不替人改已确认的数据），但要看得见。
test("requirements covered only by superseded policies are flagged, not uncovered", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/frameworks", (route) => route.fulfill({ json: [{ id: 7, key: "csf", name_zh: "", name_en: "CSF", version: "2.0", source: "nist.gov", item_count: 2, imported_at: "2026-09-09T00:00:00Z" }] }));
  await page.route("**/api/frameworks/7/tree", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/frameworks/7/coverage**", (route) => route.fulfill({ json: [
    { item_id: 1, code: "PR", title: "Protect", level: 1, parent_id: null, requirements: 2, covered: 2, superseded_only: 1 },
  ] }));
  await page.route("**/api/frameworks/7/gaps**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/frameworks/7/superseded-support**", (route) => route.fulfill({ json: [
    { item_id: 2, code: "PR.AA-01", title: "Identities", controls: ["C-0012", "C-0031"] },
  ] }));
  await page.goto("/frameworks/7");

  await expect(page.getByText("2 / 2")).toBeVisible();
  await expect(page.getByText("1 on superseded policies only")).toBeVisible();
  const card = page.locator('[data-card="superseded-support"]');
  await expect(card).toContainText("PR.AA-01");
  await expect(card).toContainText("via C-0012, C-0031");
});

test("mapping proposals show side-by-side context and highlight the quote", async ({ page }) => {
  await mockSession(page);
  const proposal = {
    id: 21, kind: "mapping", payload: {
      framework_item_id: 2, control_id: 5, strength: "partial",
      framework_item_quote: "Identities are managed", rationale: "The control covers identity management", confidence: 0.9,
    }, citations: [{ framework_item_id: 2, quote: "Identities are managed" }], confidence: 0.9,
    document_id: null, status: "pending", mapping_context: {
      framework_item: { id: 2, code: "PR.AA-01", title: "Identities", description: "Identities are managed for authorized users." },
      control: { id: 5, code: "C-0005", title: "Identity management", statement: "All identities are managed centrally." },
    },
  };
  await page.route("**/api/proposals/stats", (route) => route.fulfill({ json: { pending: 1, by_kind: { mapping: 1 } } }));
  await page.route("**/api/proposals?**", (route) => route.fulfill({ json: [proposal] }));
  await page.goto("/review?kind=mapping");
  await expect(page.getByText("Identities are managed for authorized users.")).toBeVisible();
  await expect(page.getByText("All identities are managed centrally.")).toBeVisible();
  await expect(page.locator("mark").filter({ hasText: "Identities are managed" })).toHaveCount(2);
});

test("contributors do not see framework import or mapping actions", async ({ page }) => {
  await mockSession(page, "contributor");
  await page.route("**/api/frameworks", (route) => route.fulfill({ json: [{ id: 7, key: "csf", name_zh: "CSF", name_en: "CSF", version: "2.0", source: "nist.gov", item_count: 1, imported_at: "2026-09-09T00:00:00Z" }] }));
  await page.goto("/frameworks");
  await expect(page.getByText("Import framework", { exact: true })).toHaveCount(0);
  await page.route("**/api/frameworks/7/tree", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/frameworks/7/coverage", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/frameworks/7/gaps", (route) => route.fulfill({ json: [] }));
  await page.goto("/frameworks/7");
  await expect(page.getByRole("button", { name: "Run AI mapping", exact: true })).toHaveCount(0);
});

test("control details list confirmed framework mappings", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/controls/1", (route) => route.fulfill({ json: {
    id: 1, code: "C-0001", title: "Identity management", statement: "All identities are managed centrally.",
    category: "Access", status: "active", owner_user_id: null, sources: [], relations: [],
    mappings: [{ framework_name: "CSF", code: "PR.AA-01", title: "Identities", strength: "partial" }],
  } }));
  await page.goto("/controls/1");
  await expect(page.getByRole("heading", { name: "Framework mappings" })).toBeVisible();
  await expect(page.getByText("CSF · PR.AA-01 Identities · Partially satisfies")).toBeVisible();
});

test("a supporting proposal on an already-covered item says it changes nothing", async ({ page }) => {
  await mockSession(page);
  // 412 条待审映射里 119 条是 supporting，其中 54 条指向的框架项已被 full/partial
  // 覆盖——那些项不在差距清单上，确认它们产生零信息。卡片上要看得见。
  const proposal = {
    id: 22, kind: "mapping", payload: {
      framework_item_id: 2, control_id: 5, strength: "supporting",
      framework_item_quote: "Identities are managed", rationale: "It enables but does not satisfy", confidence: 0.55,
    }, citations: [], confidence: 0.55, document_id: null, status: "pending",
    mapping_context: {
      framework_item: { id: 2, code: "PR.AA-01", title: "Identities", description: "Identities are managed for authorized users." },
      control: { id: 5, code: "C-0005", title: "Identity management", statement: "All identities are managed centrally." },
      item_coverage: { closed: true, confirmed: [{ control_code: "C-0042", strength: "partial" }] },
    },
  };
  await page.route("**/api/proposals/stats", (route) => route.fulfill({ json: { pending: 1, by_kind: { mapping: 1 } } }));
  await page.route("**/api/proposals?**", (route) => route.fulfill({ json: [proposal] }));
  await page.goto("/review?kind=mapping");
  await expect(page.getByText(/already closed by a confirmed mapping \(C-0042/)).toBeVisible();
  await expect(page.getByText(/adds nothing at all/)).toBeVisible();
});

test("an open item shows no already-covered note", async ({ page }) => {
  await mockSession(page);
  const proposal = {
    id: 23, kind: "mapping", payload: {
      framework_item_id: 2, control_id: 5, strength: "partial",
      framework_item_quote: "Identities are managed", rationale: "covers it", confidence: 0.8,
    }, citations: [], confidence: 0.8, document_id: null, status: "pending",
    mapping_context: {
      framework_item: { id: 2, code: "PR.AA-01", title: "Identities", description: "Identities are managed for authorized users." },
      control: { id: 5, code: "C-0005", title: "Identity management", statement: "All identities are managed centrally." },
      item_coverage: { closed: false, confirmed: [] },
    },
  };
  await page.route("**/api/proposals/stats", (route) => route.fulfill({ json: { pending: 1, by_kind: { mapping: 1 } } }));
  await page.route("**/api/proposals?**", (route) => route.fulfill({ json: [proposal] }));
  await page.goto("/review?kind=mapping");
  await expect(page.getByText("All identities are managed centrally.")).toBeVisible();
  await expect(page.getByText(/already closed by a confirmed mapping/)).toHaveCount(0);
});

test("a mapping proposal shows the model's reasoning and where the control came from", async ({ page }) => {
  await mockSession(page);
  // 审核者判的就是这个主张成不成立：理由藏起来等于盲判；控制点是抽取产物，
  // 看不到出处就无法发现它本身没如实反映原文（见 OQ-5）。
  const proposal = {
    id: 24, kind: "mapping", payload: {
      framework_item_id: 2, control_id: 5, strength: "partial",
      framework_item_quote: "Identities are managed",
      rationale: "The control covers central identity management but does not address recertification.",
      confidence: 0.55,
    }, citations: [], confidence: 0.55, document_id: null, status: "pending",
    mapping_context: {
      framework_item: { id: 2, code: "PR.AA-01", title: "Identities", description: "Identities are managed for authorized users." },
      control: {
        id: 5, code: "C-0005", title: "Identity management", statement: "All identities are managed centrally.",
        sources: [{ clause_id: 91, citation_label: "3.1", heading_path: "Access Control › Objectives", document_id: 4, document_title: "Acme Access Management Procedure v1.2" }],
      },
    },
  };
  await page.route("**/api/proposals/stats", (route) => route.fulfill({ json: { pending: 1, by_kind: { mapping: 1 } } }));
  await page.route("**/api/proposals?**", (route) => route.fulfill({ json: [proposal] }));
  await page.goto("/review?kind=mapping");
  const card = page.locator("#proposal-24");

  await expect(card.getByText(/does not address recertification/)).toBeVisible();
  await expect(card.getByText("Acme Access Management Procedure v1.2")).toBeVisible();
  await expect(card.getByText("Access Control › Objectives")).toBeVisible();
  await expect(card.getByRole("link", { name: "3.1" })).toHaveAttribute("href", "/documents/4#clause-91");
});

test("changing the mapping strength sticks in the dropdown", async ({ page }) => {
  await mockSession(page);
  // select 的 value 绑在 payload.strength 上，而 payload 是 AI 的原始产出、永不变；
  // onStrengthChange 只写 draft。两者不接上，选完就被弹回原值——看起来就是「改不了」。
  const proposal = {
    id: 25, kind: "mapping", payload: {
      framework_item_id: 2, control_id: 5, strength: "partial",
      framework_item_quote: "Identities are managed", rationale: "covers it", confidence: 0.8,
    }, citations: [], confidence: 0.8, document_id: null, status: "pending",
    mapping_context: {
      framework_item: { id: 2, code: "PR.AA-01", title: "Identities", description: "Identities are managed for authorized users." },
      control: { id: 5, code: "C-0005", title: "Identity management", statement: "All identities are managed centrally." },
    },
  };
  await page.route("**/api/proposals/stats", (route) => route.fulfill({ json: { pending: 1, by_kind: { mapping: 1 } } }));
  await page.route("**/api/proposals?**", (route) => route.fulfill({ json: [proposal] }));
  await page.goto("/review?kind=mapping");
  const card = page.locator("#proposal-25");
  const select = card.getByLabel("Mapping strength");

  await expect(select).toHaveValue("partial");
  await select.selectOption("supporting");
  await expect(select).toHaveValue("supporting");

  // 改动要真的进到待提交的内容里，否则「修改后接受」保存的还是原值
  await expect(card.getByLabel("Proposed content (JSON)")).toContainText('"strength": "supporting"');
});

test("each card kind says what it is claiming and what each column is", async ({ page }) => {
  await mockSession(page);
  // 三种提案共用同一个左右布局但含义不同：抽取卡右栏是「原文依据」，
  // 映射卡右栏是「你的控制点」。卡上不写出来，读卡的人只能靠猜。
  const mapping = {
    id: 26, kind: "mapping", payload: {
      framework_item_id: 2, control_id: 5, strength: "partial",
      framework_item_quote: "Identities are managed", rationale: "r", confidence: 0.8,
    }, citations: [], confidence: 0.8, document_id: null, status: "pending",
    mapping_context: {
      framework_item: { id: 2, code: "PR.AA-01", title: "Identities", description: "Identities are managed for authorized users." },
      control: { id: 5, code: "C-0005", title: "Identity management", statement: "All identities are managed centrally." },
    },
  };
  const extract = {
    id: 27, kind: "control_extract",
    payload: { title: "Log retention", statement: "Logs must be retained.", citations: [] },
    citations: [{ clause_id: 91, quote: "Logs are retained.", document_id: 4, citation_label: "3.1", document_title: "Acme Procedure" }],
    confidence: 0.6, document_id: 4, status: "pending",
  };
  await page.route("**/api/proposals/stats", (route) => route.fulfill({ json: { pending: 2, by_kind: { mapping: 1, control_extract: 1 } } }));
  await page.route("**/api/proposals?**", (route) => route.fulfill({ json: [mapping, extract] }));
  await page.goto("/review");

  await expect(page.locator("#proposal-26")).toContainText("Left: the external framework requirement");
  await expect(page.locator("#proposal-26")).toContainText("Right: your internal control");
  await expect(page.locator("#proposal-27")).toContainText("the control being proposed");
  await expect(page.locator("#proposal-27")).toContainText("your own document");
});

test("filters compose instead of clearing each other", async ({ page }) => {
  await mockSession(page);
  // 每个筛选的 onChange 曾经从零重建 query，切一个就把别的清空。
  // 只有两个筛选时不易察觉，加到四个必然显形。
  const seen: string[] = [];
  await page.route("**/api/frameworks", (route) => route.fulfill({
    json: [{ id: 3, key: "nist-csf-2.0", name_zh: "CSF", name_en: "NIST CSF 2.0", version: "2.0", source: "nist.gov", item_count: 134, imported_at: "2026-09-09T00:00:00Z" }],
  }));
  await page.route("**/api/proposals/stats", (route) => route.fulfill({ json: { pending: 0, by_kind: { mapping: 0 } } }));
  await page.route("**/api/proposals?**", (route) => {
    seen.push(new URL(route.request().url()).search);
    return route.fulfill({ json: [] });
  });
  await page.goto("/review?kind=mapping");

  // exact 必需：卡片内的强度下拉框叫 "Mapping strength"，子串匹配会一并命中。
  await page.getByLabel("Strength", { exact: true }).selectOption("full,partial");
  await page.getByLabel("Framework", { exact: true }).selectOption("nist-csf-2.0");
  await page.getByText("Reasoning hedges").click();

  const last = seen[seen.length - 1];
  expect(last).toContain("kind=mapping");
  expect(last).toContain("strength=full");
  expect(last).toContain("strength=partial");
  expect(last).toContain("framework=nist-csf-2.0");
  expect(last).toContain("doubtful_rationale=true");
});

test("switching away from mapping drops the mapping-only filters", async ({ page }) => {
  await mockSession(page);
  // strength 对关系提案没有意义，留着会让它一条也筛不出来。
  const seen: string[] = [];
  await page.route("**/api/frameworks", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/proposals/stats", (route) => route.fulfill({ json: { pending: 0, by_kind: { mapping: 0, relation: 0 } } }));
  await page.route("**/api/proposals?**", (route) => {
    seen.push(new URL(route.request().url()).search);
    return route.fulfill({ json: [] });
  });
  await page.goto("/review?kind=mapping&strength=full&framework=nist-csf-2.0&doubtful_rationale=1");

  await page.getByLabel("Proposal type").selectOption("relation");
  const last = seen[seen.length - 1];
  expect(last).toContain("kind=relation");
  expect(last).not.toContain("strength=");
  expect(last).not.toContain("framework=");
  expect(last).not.toContain("doubtful_rationale=");
});

test("the documents page calls out corpus blind spots", async ({ page }) => {
  await mockSession(page);
  // 两种盲区都是静默的：文档传了没抽取、规范条款没产出控制点。
  // 不显示出来，界面上没有任何地方会提示。
  const docs = [1, 2, 3].map((id) => ({
    id, title: `Doc ${id}`, doc_type: "procedure", status: "active", version: null,
    owner: null, approver: null, approved_date: null, effective_date: null,
    review_due_date: null, original_filename: `${id}.pdf`, parse_error: null,
  }));
  // 一个 handler 按 pathname 分派：分开注册时 "**/api/documents" 会把
  // "/api/documents/coverage" 之外的列表请求一并兜掉，顺序还依赖注册次序。
  await page.route("**/api/documents**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/documents/coverage") {
      return route.fulfill({ json: [
        { document_id: 1, clauses: 19, normative_clauses: 9, controls: 0, proposals: 0, uncovered_normative: 9, never_extracted: true },
        { document_id: 2, clauses: 37, normative_clauses: 22, controls: 54, proposals: 59, uncovered_normative: 11, never_extracted: false },
        { document_id: 3, clauses: 21, normative_clauses: 1, controls: 16, proposals: 16, uncovered_normative: 0, never_extracted: false },
      ] });
    }
    return route.fulfill({ json: docs });
  });
  await page.goto("/documents");

  await expect(page.getByText("Never extracted")).toBeVisible();
  await expect(page.getByText("54 controls")).toBeVisible();
  await expect(page.getByText("11 normative clauses with no control")).toBeVisible();
  // 完全覆盖的那份不该出现警示
  await expect(page.getByText("0 normative clauses with no control")).toHaveCount(0);
});

test("an extraction proposal stated more strongly than its source is flagged", async ({ page }) => {
  await mockSession(page);
  // 引用校验查不到这个：引文逐字正确，被改的是结论里的情态动词。
  const proposal = {
    id: 40, kind: "control_extract",
    payload: { title: "Review board cadence", statement: "The review board must meet every quarter.", citations: [] },
    citations: [{ clause_id: 7, quote: "The review board meets every quarter", document_id: 3, citation_label: "4.4", document_title: "Change Management" }],
    confidence: 0.95, document_id: 3, status: "pending", normative_drift: true,
    bulk_acceptable: false, extraction_review: { document_id: 3, inventory_id: 8, finding_count: 2 },
  };
  await page.route("**/api/proposals/stats", (route) => route.fulfill({ json: { pending: 1, by_kind: { control_extract: 1 } } }));
  await page.route("**/api/proposals?**", (route) => route.fulfill({ json: [proposal] }));
  await page.goto("/review?kind=control_extract");
  await expect(page.locator("#proposal-40").getByText(/Stated more strongly than the source/)).toBeVisible();
  await expect(page.locator("#proposal-40").getByText(/Source review flagged 2 possible/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Inspect source review findings" })).toHaveAttribute("href", "/documents/3");
  await expect(page.getByRole("checkbox", { name: /40/ })).toBeDisabled();
});
