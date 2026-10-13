import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function setup(page: Page, available = true) {
  await page.addInitScript(() => localStorage.setItem("grc.token", "mock-token"));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: {
      id: 1, email: "reader@example.com", name: "Reader", role: "viewer",
    } });
    if (url.pathname === "/api/documents/3") return route.fulfill({ json: {
      id: 3, title: "Review policy", status: "active", doc_type: "policy",
      parse_warnings: null, parse_warning_codes: null, supersedes_id: null,
    } });
    if (url.pathname === "/api/documents/3/clauses") return route.fulfill({ json: [{
      id: 11, number: "1", heading: "Review", heading_path: "Review", citation_label: "1",
      text: "Edited clause text.", level: 1, kind: "section", children: [],
    }] });
    if (url.pathname === "/api/extraction/documents/3/coverage") return route.fulfill({ json: {
      counts: { cited: 0, uncited: 0, not_completed: 1 }, clauses: [],
    } });
    if (url.pathname.endsWith("/source-blocks")) return route.fulfill({ json: {
      available, total: available ? 1 : 0, unassigned: 0, has_more: false,
      blocks: available ? [{ id: 21, text: "Original complete review requirement.",
        origin: "docx_xml", kind: "table_cell", clause_ids: [11],
        locator: { body_index: 4, table: 1, positions: [{ row: 2, column: 1 }] },
      }] : [],
    } });
    return route.fulfill({ json: [] });
  });
  await page.goto("/documents/3");
}

test("原文块与编辑后的条款分别显示，并保留表格位置", async ({ page }) => {
  await setup(page);
  await expect(page.getByText("Edited clause text.", { exact: true })).toBeVisible();
  await page.getByText("Clause source blocks", { exact: true }).click();
  await expect(page.getByText("Original complete review requirement.", { exact: true })).toBeVisible();
  await expect(page.getByText(/Table 1 · Row 2, column 1/)).toBeVisible();
  await expect(page.getByText(/not exact quote boundaries/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Next blocks" })).toBeDisabled();
});

test("旧文档明确说明没有原文快照", async ({ page }) => {
  await setup(page, false);
  await page.getByText("Document source blocks", { exact: true }).click();
  await expect(page.getByText(/No source snapshot is available/)).toBeVisible();
  await expect(page.getByText("0 source blocks", { exact: false })).toHaveCount(0);
});


test("已引用的条款仍展示缺失的跨章节引用", async ({ page }) => {
  await setup(page);
  await page.route("**/api/extraction/documents/3/coverage", (route) => route.fulfill({ json: {
    counts: { cited: 1, uncited: 0, not_completed: 0 },
    clauses: [{ clause_id: 11, citation_label: "1", status: "cited" }],
    reference_issues: [{ source_clause_id: 11, label: "9", status: "missing" }],
  } }));
  await page.reload();
  await expect(page.getByText("Internal references need review", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "1 → 9 · Referenced clause not found" }).click();
  await expect(page.getByText("Edited clause text.", { exact: true })).toBeVisible();
});


test("要求清单显示原文限定，待确认状态不会被已引用掩盖", async ({ page }) => {
  await setup(page);
  await page.route("**/api/extraction/documents/3/coverage", (route) => route.fulfill({ json: {
    counts: { cited: 1, uncited: 0, not_completed: 0 },
    clauses: [{ clause_id: 11, citation_label: "1", status: "cited",
      inventory_id: 7, inventory_status: "needs_review" }],
  } }));
  await page.route("**/api/extraction/documents/3/inventories/7", (route) => route.fulfill({ json: {
    requirements: [{ id: "R1", statement: "Owners should review quarterly except emergency access.",
      target_clause_ids: [11], components: { subject: "Owners", action: "review",
        frequency: "quarterly", exceptions: "except emergency access", deadline: null },
      citations: [{ clause_id: 11, quote: "Owners should review quarterly except emergency access." }],
    }],
  } }));
  await page.reload();
  await expect(page.getByText("Requirements needing human review", { exact: true })).toBeVisible();
  await page.getByText("Requirement inventory", { exact: true }).click();
  await expect(page.getByText("quarterly", { exact: true })).toBeVisible();
  await expect(page.getByText("except emergency access", { exact: true })).toBeVisible();
  await expect(page.getByText("Not identified in the source", { exact: true })).toBeVisible();
  await expect(page.getByText(/This does not prove that the model found every duty/)).toBeVisible();
});


test("空要求清单也展示查漏发现，复核未完成不会显示没有问题", async ({ page }) => {
  await setup(page);
  await page.route("**/api/extraction/documents/3/coverage", (route) => route.fulfill({ json: {
    counts: { cited: 0, uncited: 1, not_completed: 0 },
    clauses: [{ clause_id: 11, citation_label: "1", status: "uncited",
      inventory_id: 8, inventory_status: "needs_review" }],
  } }));
  await page.route("**/api/extraction/documents/3/inventories/8", (route) => route.fulfill({ json: {
    requirements: [], normalized_controls: [], semantic_review: { findings: [{
      kind: "missing_requirement", clause_ids: [11], requirement_ids: [], control_indexes: [],
      detail: "The duty to keep review records was omitted.",
      citations: [{ clause_id: 11, quote: "Retain review records for three years." }],
    }] },
  } }));
  await page.reload();
  await page.getByText("Requirement inventory", { exact: true }).click();
  await expect(page.getByText("Possible omitted requirement", { exact: true })).toBeVisible();
  await expect(page.getByText("Retain review records for three years.", { exact: true })).toBeVisible();
  await expect(page.getByText(/does not certify completeness/)).toHaveCount(0);
  await page.route("**/api/extraction/documents/3/inventories/8", (route) => route.fulfill({ json: {
    requirements: [], normalized_controls: [], semantic_review: null,
  } }));
  await page.reload();
  await page.getByText("Requirement inventory", { exact: true }).click();
  await expect(page.getByText("Semantic review has not completed for this inventory.", { exact: true })).toBeVisible();
  await expect(page.getByText(/does not certify completeness/)).toHaveCount(0);
});
