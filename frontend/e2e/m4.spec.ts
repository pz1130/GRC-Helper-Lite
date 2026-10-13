import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// 每一个 API 请求都被拦截：这些用例从不碰应用库。
async function mockSession(page: Page, role = "grc_lead") {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    if (new URL(route.request().url()).pathname === "/api/auth/me") {
      await route.fulfill({ json: { id: 1, email: "lead@example.com", name: "Lead", role } });
    } else {
      await route.fulfill({ status: 500, json: { message: `Unexpected API: ${route.request().url()}` } });
    }
  });
}

const proposal = (id: number, bulk_acceptable = false, ocr_quality_flag = false) => ({
  id, kind: "control_extract", payload: { title: `Control ${id}`, statement: "Two approvers", citations: [{ clause_id: 7, quote: "Two approvers" }] },
  citations: [{ clause_id: 7, quote: "Two approvers" }], confidence: 0.95, document_id: 9,
  status: "pending", bulk_acceptable, ocr_quality_flag,
});

test("review honors server eligibility, edits payload, requires rejection reason and reports batch results", async ({ page }) => {
  await mockSession(page);
  let pending = [proposal(1, true), proposal(2, true, true), proposal(3)];
  const decisions: unknown[] = [];
  await page.route("**/api/proposals**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/proposals/stats") return route.fulfill({ json: { pending: pending.length, by_kind: { control_extract: pending.length } } });
    if (path.endsWith("/decide")) {
      const body = route.request().postDataJSON(); decisions.push(body);
      const id = Number(path.split("/")[3]); pending = pending.filter((p) => p.id !== id);
      return route.fulfill({ json: { ...proposal(id), status: body.decision === "reject" ? "rejected" : "modified" } });
    }
    if (path.endsWith("bulk-accept")) {
      expect(route.request().postDataJSON()).toEqual({ ids: [1] });
      pending = pending.filter((p) => p.id !== 1);
      return route.fulfill({ json: { accepted: 1, skipped: 0 } });
    }
    return route.fulfill({ json: pending });
  });
  await page.goto("/review");
  await expect(page.getByLabel("Select proposal 1")).toBeEnabled();
  await expect(page.getByLabel("Select proposal 2")).toBeDisabled();
  await expect(page.getByLabel("Select proposal 3")).toBeDisabled();
  await expect(page.locator("#proposal-2")).toContainText("OCR quality is uncertain");
  await expect(page.locator("#proposal-1 a")).toHaveAttribute("href", "/documents/9#clause-7");
  const third = page.locator("#proposal-3");
  await third.getByRole("button", { name: "Edit and accept" }).click();
  await third.getByRole("textbox").fill("{");
  await third.getByRole("button", { name: "Edit and accept" }).click();
  await expect(page.getByRole("alert")).toContainText("non-empty JSON object");
  await third.getByRole("textbox").fill(JSON.stringify({ title: "Edited title", statement: "Two approvals" }));
  await third.getByRole("button", { name: "Edit and accept" }).click();
  await expect(third).toHaveCount(0);
  expect(decisions[0]).toMatchObject({ decision: "modify", payload: { title: "Edited title", statement: "Two approvals", citations: [{ clause_id: 7, quote: "Two approvers" }] } });
  const second = page.locator("#proposal-2");
  await second.getByRole("button", { name: "Reject", exact: true }).click();
  await expect(second.getByRole("button", { name: "Reject", exact: true })).toBeDisabled();
  await second.getByRole("textbox").fill("  Wrong interpretation  ");
  await second.getByRole("button", { name: "Reject", exact: true }).click();
  await expect(second).toHaveCount(0);
  expect(decisions[1]).toEqual({ decision: "reject", reason: "Wrong interpretation" });
  await page.getByLabel("Select proposal 1").check();
  await page.getByRole("button", { name: "Accept selected (1)" }).click();
  await expect(page.getByRole("status")).toContainText("Accepted 1; skipped 0");
  await expect(page.getByText("No pending proposals in this view")).toBeVisible();
});

test("a proposal whose statement already exists offers merge or a separate control", async ({ page }) => {
  await mockSession(page);
  const twin = { ...proposal(4), duplicate_of: { id: 19, code: "C-0019", title: "Business continuity requirements" } };
  let pending = [twin];
  const decisions: unknown[] = [];
  await page.route("**/api/proposals**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/proposals/stats") return route.fulfill({ json: { pending: pending.length, by_kind: { control_extract: pending.length } } });
    if (path.endsWith("/decide")) {
      decisions.push(route.request().postDataJSON());
      pending = [];
      return route.fulfill({ json: { ...twin, status: "accepted" } });
    }
    return route.fulfill({ json: pending });
  });
  await page.goto("/review");

  const card = page.locator("#proposal-4");
  await expect(card).toContainText("C-0019 already says exactly this");
  // 需要逐条选择的东西，批量按钮给不了。
  await expect(page.getByLabel("Select proposal 4")).toBeDisabled();
  await expect(card.getByRole("button", { name: "Create a separate control" })).toBeVisible();

  await card.getByRole("button", { name: "Merge into C-0019" }).click();
  await expect(card).toHaveCount(0);
  expect(decisions[0]).toEqual({ decision: "accept", merge_into_control_id: 19 });
});

test("contributors can read review and controls without mutation actions", async ({ page }) => {
  await mockSession(page, "contributor");
  await page.route("**/api/proposals**", (route) => route.fulfill({ json: route.request().url().includes("stats") ? { pending: 1, by_kind: {} } : [proposal(1, true)] }));
  await page.route("**/api/controls**", (route) => route.fulfill({ json: [] }));
  await page.goto("/review");
  await expect(page.getByText("Control 1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Accept", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Select proposal 1")).toHaveCount(0);
  await page.getByRole("link", { name: "Controls", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Controls" })).toBeVisible();
  await expect(page.getByText("Import an Excel control matrix")).toHaveCount(0);
  await expect(page.getByText("Extract controls", { exact: true })).toHaveCount(0);
});

test("single accept reports a failure and supports retry without hiding the proposal", async ({ page }) => {
  await mockSession(page);
  let attempts = 0;
  let accepted = false;
  await page.route("**/api/proposals**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/decide")) {
      expect(route.request().postDataJSON()).toEqual({ decision: "accept" });
      attempts += 1;
      if (attempts === 1) return route.fulfill({ status: 409, json: { message: "Please retry the decision" } });
      accepted = true;
      return route.fulfill({ json: { ...proposal(1), status: "accepted" } });
    }
    return route.fulfill({ json: path.endsWith("stats") ? { pending: accepted ? 0 : 1, by_kind: {} } : accepted ? [] : [proposal(1)] });
  });
  await page.goto("/review");
  await page.getByRole("button", { name: "Accept", exact: true }).click();
  // 改造后告警带 ⚠️ 前缀；这里要断言的是「错误如实呈现」，不是「一个字符不差」。
  await expect(page.getByRole("alert")).toContainText("Please retry the decision");
  await expect(page.locator("#proposal-1")).toBeVisible();
  await page.getByRole("button", { name: "Accept", exact: true }).click();
  await expect(page.locator("#proposal-1")).toHaveCount(0);
  await expect(page.getByRole("status")).toHaveText("Decision saved.");
});

test("extraction only offers active documents and links the queued job to document review", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/controls**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/documents", (route) => route.fulfill({ json: [{ id: 9, title: "Policy", status: "active" }, { id: 10, title: "Unparsed", status: "uploaded" }] }));
  await page.route("**/api/extraction/documents/9", (route) => {
    expect(route.request().method()).toBe("POST");
    return route.fulfill({ json: { job_id: "extract-9" } });
  });
  await page.goto("/controls");
  await page.locator("summary").filter({ hasText: "Extract controls" }).click();
  await expect(page.getByRole("button", { name: "Extract controls" })).toBeDisabled();
  await expect(page.getByRole("option", { name: "Unparsed" })).toHaveCount(0);
  await page.getByLabel("Choose an active document").selectOption("9");
  await page.getByRole("button", { name: "Extract controls" }).click();
  await expect(page.getByRole("status")).toContainText("Extraction queued (extract-9)");
  await expect(page.locator('a[href="/review?document_id=9"]')).toHaveText("Review queue");
});

test("matrix validates, approves changed mapping with provenance, imports using proposal_id", async ({ page }) => {
  await mockSession(page);
  const payload = { mapping: { title: "Name", statement: "Description" }, source_sha256: "file-hash", source_headers: ["Name", "Description", "Category"], source_rows: 2 };
  const mappingProposal = { ...proposal(20), kind: "matrix_mapping", document_id: null, citations: [], payload };
  await page.route("**/api/controls**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/documents", (route) => route.fulfill({ json: [{ id: 9, title: "Policy", status: "active" }] }));
  await page.route("**/api/matrix/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    expect(route.request().headers()["content-type"]).toContain("multipart/form-data; boundary=");
    if (path.endsWith("propose-mapping")) return route.fulfill({ json: mappingProposal });
    if (path.endsWith("validate")) return route.fulfill({ json: { ok: true, report: [], rows: 2 } });
    expect(route.request().postDataBuffer()?.toString()).toContain('name="proposal_id"\r\n\r\n20');
    expect(route.request().postDataBuffer()?.toString()).not.toContain('name="mapping_json"');
    return route.fulfill({ json: { imported: 2, proposal_ids: [21, 22], mapping_proposal_id: 20 } });
  });
  await page.route("**/api/proposals/20/decide", async (route) => {
    expect(route.request().postDataJSON()).toEqual({ decision: "modify", payload: { ...payload, mapping: { ...payload.mapping, category: "Category" } } });
    await route.fulfill({ json: { ...mappingProposal, status: "modified", decided_payload: route.request().postDataJSON().payload } });
  });
  await page.goto("/controls");
  await page.getByText("Import an Excel control matrix", { exact: true }).click();
  await page.getByLabel("Excel file").setInputFiles({ name: "controls.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("mock file") });
  await page.getByRole("button", { name: "Propose column mapping" }).click();
  await page.getByLabel("Category", { exact: true }).selectOption("Category");
  await expect(page.getByRole("button", { name: "Approve mapping" })).toBeDisabled();
  await page.getByRole("button", { name: "Validate all rows" }).click();
  await expect(page.getByText("Validation passed for 2 rows.")).toBeVisible();
  await page.getByRole("button", { name: "Approve mapping" }).click();
  await expect(page.getByText("Mapping approved. Import now to create pending control proposals.")).toBeVisible();
  await page.getByRole("button", { name: "Import as control proposals" }).click();
  await expect(page.getByText("Created 2 control proposals.", { exact: false })).toBeVisible();
});

test("control detail links exact clauses and directed relationships; errors can retry", async ({ page }) => {
  await mockSession(page);
  let failed = true;
  await page.route("**/api/controls/1", (route) => route.fulfill(failed ? { status: 500, json: { message: "Temporarily unavailable" } } : { json: { id: 1, code: "AC-01", title: "Dual approval", statement: "Two approvals", category: "Access", status: "active", owner_user_id: null, sources: [{ clause_id: 7, document_id: 9, document_title: "Policy", citation_label: "4.1", heading_path: "Policy > Access", relation: "defines" }], relations: [{ from_control_id: 2, to_control_id: 1, relation_type: "implements", rationale: "Procedure implements policy" }] } }));
  await page.goto("/controls/1");
  await expect(page.getByRole("alert")).toContainText("Temporarily unavailable", { timeout: 15000 });
  failed = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("link", { name: "Policy · 4.1" })).toHaveAttribute("href", "/documents/9#clause-7");
  await expect(page.getByRole("link", { name: "#2", exact: true })).toHaveAttribute("href", "/controls/2");
  await expect(page.getByText("Procedure implements policy")).toBeVisible();
});

const controlDetail = (id: number, code: string, title: string, status = "active", merged_into_id: number | null = null) => ({
  id, code, title, statement: title, category: "Access", status, owner_user_id: null, merged_into_id,
  sources: [], relations: [], mappings: [], implementations: [], evidence: [],
});

test("merging a control previews what moves and what is dropped", async ({ page }) => {
  await mockSession(page);
  const merges: unknown[] = [];
  await page.route("**/api/controls**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/controls/2/merge-preview") {
      return route.fulfill({
        json: {
          loser_code: "C-0002",
          winner_code: "C-0001",
          moves: { mappings: 1 },
          // 真实后端的形状：detail 是中文兜底，句子由前端按 code 组。
          // 这里曾经是一句英文 detail，于是这条测试看不见英文界面冒中文。
          discards: [{
            table: "mappings", id: 9, detail: "PR.AA-01（partial）——赢家已有 full",
            code: "merge.discard.mapping_duplicate",
            params: { item: "PR.AA-01", strength: "partial", existing: "full" },
          }],
          blockers: [],
          plan_digest: "a".repeat(64),
        },
      });
    }
    if (path === "/api/controls/2/merge" && route.request().method() === "POST") {
      merges.push(route.request().postDataJSON());
      return route.fulfill({ json: { loser_code: "C-0002", winner_code: "C-0001", moves: { mappings: 1 }, discards: [], blockers: [] } });
    }
    if (path === "/api/controls/2") return route.fulfill({ json: controlDetail(2, "C-0002", "Duplicate access") });
    if (path === "/api/controls/1") return route.fulfill({ json: controlDetail(1, "C-0001", "Access control") });
    if (path === "/api/controls") return route.fulfill({ json: [controlDetail(1, "C-0001", "Access control"), controlDetail(2, "C-0002", "Duplicate access")] });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url}` } });
  });
  await page.goto("/controls/2");
  await page.getByRole("button", { name: /Merge into/ }).click();
  await page.getByRole("button", { name: /C-0001/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("PR.AA-01 (partial): the winner already maps it as full");
  await expect(dialog).not.toContainText("赢家");
  await dialog.getByRole("button", { name: "Merge" }).click();
  // 带回预览时的指纹：后端重算的计划与它不同就拒绝。
  expect(merges[0]).toEqual({ into_control_id: 1, plan_digest: "a".repeat(64) });
});

test("a merge whose plan changed after the preview is re-previewed, not run", async ({ page }) => {
  await mockSession(page);
  let previews = 0;
  await page.route("**/api/controls**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/controls/2/merge-preview") {
      previews += 1;
      return route.fulfill({ json: {
        loser_code: "C-0002", winner_code: "C-0001", moves: {}, blockers: [],
        discards: previews === 1 ? [] : [{
          table: "mappings", id: 9, detail: "PR.AA-01（partial）——赢家已有 full",
          code: "merge.discard.mapping_duplicate",
          params: { item: "PR.AA-01", strength: "partial", existing: "full" },
        }],
        plan_digest: (previews === 1 ? "a" : "b").repeat(64),
      } });
    }
    if (path === "/api/controls/2/merge" && route.request().method() === "POST") {
      return route.fulfill({ status: 409, json: { code: "control.merge_plan_changed", message: "预览之后数据有变化" } });
    }
    if (path === "/api/controls/2") return route.fulfill({ json: controlDetail(2, "C-0002", "Duplicate access") });
    if (path === "/api/controls") return route.fulfill({ json: [controlDetail(1, "C-0001", "Access control"), controlDetail(2, "C-0002", "Duplicate access")] });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url}` } });
  });
  await page.goto("/controls/2");
  await page.getByRole("button", { name: /Merge into/ }).click();
  await page.getByRole("button", { name: /C-0001/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).not.toContainText("PR.AA-01");
  await dialog.getByRole("button", { name: "Merge" }).click();

  // 被拒后按最新数据重算：新出现的丢弃项要让人看见，原因也要说出来。
  await expect(dialog).toContainText("PR.AA-01 (partial): the winner already maps it as full");
  await expect(dialog.getByRole("alert")).toContainText("The data changed after the preview");
  expect(previews).toBe(2);
});

test("a merged control says where it went instead of offering to merge again", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/controls**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/controls/2") return route.fulfill({ json: controlDetail(2, "C-0002", "Duplicate access", "merged", 1) });
    if (path === "/api/controls/1") return route.fulfill({ json: controlDetail(1, "C-0001", "Access control") });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${route.request().url()}` } });
  });
  await page.goto("/controls/2");
  await expect(page.getByRole("note")).toContainText("Merged into C-0001");
  await expect(page.getByRole("button", { name: /Merge into/ })).toHaveCount(0);
});

test("failed decisions surface the server error and keep the proposal pending", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/proposals**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/decide")) return route.fulfill({ status: 409, json: { code: "conflict", message: "A confirmed mapping already exists" } });
    if (path.endsWith("/stats")) return route.fulfill({ json: { pending: 1, by_kind: { control_extract: 1 } } });
    return route.fulfill({ json: [proposal(1)] });
  });
  await page.goto("/review");
  await page.locator("#proposal-1").getByRole("button", { name: "Accept", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("A confirmed mapping already exists");
  await expect(page.locator("#proposal-1")).toBeVisible();
  await expect(page.getByText("Decision saved.", { exact: true })).toHaveCount(0);
});

test("failed extraction batches are visible in the queue and can be retried", async ({ page }) => {
  // OQ-22：失败批次原先只把 rejected 计数 +1 就跳过，错误落进 llm_call.error，
  // 界面只说"任务已入队"。生产栈实测 7 批丢 2 批，审计员无从知道。
  await mockSession(page);
  let retried = 0;
  await page.route("**/api/proposals**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/proposals/stats") {
      return route.fulfill({ json: { pending: 0, by_kind: {}, failed: 1 } });
    }
    if (path === "/api/proposals/failures") {
      return route.fulfill({ json: [{
        id: 91, document_id: 9, llm_call_id: 7, clause_ids: [4, 5, 6],
        reject_reason: "schema: 模型输出中没有找到合法的 JSON 对象",
      }] });
    }
    return route.fulfill({ json: [] });
  });
  await page.route("**/api/extraction/documents/9", (route) => {
    retried += 1;
    return route.fulfill({ json: { job_id: "retry-42" } });
  });

  await page.goto("/review");

  // 看得见，而且说得出是哪一批、为什么
  await expect(page.getByText("1 batch(es) produced no controls")).toBeVisible();
  await expect(page.getByText("模型输出中没有找到合法的 JSON 对象")).toBeVisible();
  await expect(page.getByText("3 clause(s) affected")).toBeVisible();

  // 待确认角标不被失败行撑大
  await expect(page.getByText("Pending 0")).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Retry queued (retry-42)")).toBeVisible();
  expect(retried).toBe(1);
});
