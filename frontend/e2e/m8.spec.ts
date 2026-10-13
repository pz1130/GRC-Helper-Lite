import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function mockAudit(page: Page, withAnswer = false) {
  await page.addInitScript(() => {
    localStorage.setItem("grc.token", "mock-token");
  });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { id: 1, email: "lead@example.com", name: "Lead", role: "grc_lead" } });
    if (url.pathname === "/api/audit/engagements") return route.fulfill({ json: [{ id: 1, name: "ISO audit", audit_type: "external", status: "preparing" }] });
    if (url.pathname === "/api/audit/engagements/1/questions/xlsx") return route.fulfill({ status: 201, json: [{ id: 9, seq: 2, question_text: "Imported question", language: "en", status: "pending" }] });
    if (url.pathname === "/api/audit/engagements/1/export.docx") return route.fulfill({ body: "fake-docx", contentType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
    if (url.pathname === "/api/audit/engagements/1/generate-all") return route.fulfill({ json: { job_id: "audit-job-1", question_count: 1 } });
    if (url.pathname === "/api/audit/engagements/1/preflight") return route.fulfill({ json: { engagement_id: 1, summary: { green: 1, yellow: 1, red: 1 }, rows: [
      { framework_item_id: 1, code: "PR.AA-01", title: "Identity management", likely_question: "How are identities managed?", readiness: "green", reason: "Valid evidence is available", control_count: 2, implementation_count: 1, evidence_count: 1, valid_evidence_count: 1, expired_evidence_count: 0, evidence_titles: ["Review report"], tool_names: ["VaultKeeper"] },
      { framework_item_id: 2, code: "PR.AA-02", title: "Access review", likely_question: "How is access reviewed?", readiness: "yellow", reason: "Implementation exists, but valid evidence is missing", control_count: 1, implementation_count: 1, evidence_count: 0, valid_evidence_count: 0, expired_evidence_count: 0, evidence_titles: [], tool_names: ["Entra ID"] },
      { framework_item_id: 3, code: "PR.AA-03", title: "Authentication", likely_question: "How is authentication protected?", readiness: "red", reason: "No implementation or valid evidence is registered", control_count: 0, implementation_count: 0, evidence_count: 0, valid_evidence_count: 0, expired_evidence_count: 0, evidence_titles: [], tool_names: [] },
    ] } });
    if (url.pathname === "/api/audit/engagements/1/questions") return route.fulfill({ json: [{ id: 2, seq: 1, question_text: "How is access reviewed?", language: "en", status: withAnswer ? "drafted" : "pending" }] });
    if (url.pathname === "/api/audit/questions/2/answer") return route.fulfill({ json: withAnswer ? { id: 3, question_id: 2, body: "Access is reviewed quarterly.", language: "en", gap_notes: "Evidence is not registered.", cited_clause_ids: [4], cited_control_ids: [5], suggested_evidence_ids: [], confidence: 0.9, final_body: null, finalized_at: null } : null });
    if (url.pathname === "/api/audit/questions/2/similar-history") return route.fulfill({ json: [{ answer_id: 8, question_id: 7, question_text: "How was access reviewed last year?", engagement_name: "Prior audit", answer: "Access was reviewed quarterly.", language: "en", finalized_at: "2026-01-01T00:00:00Z", similarity: 0.67 }] });
    if (url.pathname === "/api/audit/history") return route.fulfill({ json: [] });
    if (url.pathname === "/api/audit/answers/3/finalize") return route.fulfill({ json: {} });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url.pathname}` } });
  });
}

test("audit workspace shows engagement questions and guarded generation", async ({ page }) => {
  await mockAudit(page);
  await page.goto("/audit");
  await expect(page.getByRole("heading", { name: "Audit response workspace" })).toBeVisible();
  await expect(page.getByText("ISO audit")).toBeVisible();
  await expect(page.getByRole("heading", { name: "How is access reviewed?" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Draft in English" })).toBeVisible();
  await expect(page.getByText("No formal draft yet. Generated answers enter the review queue first.")).toBeVisible();
  await expect(page.getByText("How was access reviewed last year?")).toBeVisible();
  await page.getByRole("button", { name: "Generate all drafts" }).click();
  await expect(page.getByRole("status")).toContainText("Queued 1 questions");
});

test("accepted draft can be edited and finalized", async ({ page }) => {
  await mockAudit(page, true);
  await page.goto("/audit");
  await expect(page.getByLabel("Answer body")).toHaveValue("Access is reviewed quarterly.");
  await expect(page.getByText("Evidence is not registered.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Finalize" })).toBeVisible();
});

test("questions can be imported from Excel and finalized answers exported", async ({ page }) => {
  await mockAudit(page, true);
  await page.goto("/audit");
  await page.getByLabel("Excel question file").setInputFiles({
    name: "questions.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("xlsx fixture"),
  });
  await page.getByRole("button", { name: "Import from Excel" }).click();
  await expect(page.getByRole("status")).toContainText("Imported 1 questions from Excel.");

  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export Word response package" }).click();
  const file = await downloaded;
  expect(file.suggestedFilename()).toBe("ISO audit-audit-responses.docx");
});

test("audit preflight shows and filters computed readiness", async ({ page }) => {
  await mockAudit(page);
  await page.goto("/audit");
  await page.getByRole("button", { name: "Audit preflight" }).click();
  await expect(page.getByRole("heading", { name: "Audit readiness preview" })).toBeVisible();
  await expect(page.getByText("PR.AA-01 Identity management")).toBeVisible();
  await page.getByRole("button", { name: "Implemented, evidence missing (1)" }).click();
  await expect(page.getByText("PR.AA-02 Access review")).toBeVisible();
  await expect(page.getByText("PR.AA-01 Identity management")).toBeHidden();
});

// 定稿原来不带内容，后端拿库里的正文定稿：页面上改了没保存就点定稿，定下来的
// （也是导出给审计师的）是旧文本，改动在刷新后悄悄消失。
test("finalizing sends the text on screen, and waits for unsaved edits to be saved", async ({ page }) => {
  await mockAudit(page, true);
  let finalized: unknown = null;
  await page.route("**/api/audit/answers/3/finalize", (route) => {
    finalized = route.request().postDataJSON();
    return route.fulfill({ json: {} });
  });
  await page.goto("/audit");
  const finalize = page.getByRole("button", { name: "Finalize" });
  await expect(page.getByLabel("Answer body")).toHaveValue("Access is reviewed quarterly.");

  await page.getByLabel("Answer body").fill("Access is reviewed monthly.");
  await expect(finalize).toBeDisabled();
  await expect(page.getByText("You have unsaved edits: save them before finalizing")).toBeVisible();

  await page.getByLabel("Answer body").fill("Access is reviewed quarterly.");
  await expect(finalize).toBeEnabled();
  await finalize.click();
  await expect.poll(() => finalized).toEqual({ body: "Access is reviewed quarterly." });
});

// 答复每 10 秒刷新一次。原来一刷新就把编辑框换成库里的版本：别人一保存，你正在
// 打的字就没了。现在改过的保留、另给提示；没改过的跟着新版本走。
test.describe("an answer someone else saves while you edit", () => {
  function answerRoute(page: Page, body: () => string) {
    return page.route("**/api/audit/questions/2/answer", (route) => route.fulfill({ json: {
      id: 3, question_id: 2, body: body(), language: "en", gap_notes: "", cited_clause_ids: [4],
      cited_control_ids: [], suggested_evidence_ids: [], confidence: 0.9, final_body: null, finalized_at: null,
    } }));
  }

  test("keeps what you typed and offers their version", async ({ page }) => {
    await page.clock.install();
    await mockAudit(page, true);
    let server = "Access is reviewed quarterly.";
    await answerRoute(page, () => server);
    await page.goto("/audit");
    const box = page.getByLabel("Answer body");
    await expect(box).toHaveValue("Access is reviewed quarterly.");

    await box.fill("Access is reviewed quarterly by the owner.");
    server = "Access is reviewed monthly.";
    await page.clock.fastForward(11_000);

    await expect(page.getByText("Someone saved a newer version of this answer.")).toBeVisible();
    await expect(box).toHaveValue("Access is reviewed quarterly by the owner.");

    await page.getByRole("button", { name: "Load their version (discard mine)" }).click();
    await expect(box).toHaveValue("Access is reviewed monthly.");
  });

  test("follows the newer version when you had not changed anything", async ({ page }) => {
    await page.clock.install();
    await mockAudit(page, true);
    let server = "Access is reviewed quarterly.";
    await answerRoute(page, () => server);
    await page.goto("/audit");
    const box = page.getByLabel("Answer body");
    await expect(box).toHaveValue("Access is reviewed quarterly.");

    server = "Access is reviewed monthly.";
    await page.clock.fastForward(11_000);
    await expect(box).toHaveValue("Access is reviewed monthly.");
    await expect(page.getByText("Someone saved a newer version of this answer.")).toHaveCount(0);
  });

  test("saving sends the version the edit started from", async ({ page }) => {
    await mockAudit(page, true);
    let sent: unknown = null;
    await page.route("**/api/audit/answers/3", (route) => {
      sent = route.request().postDataJSON();
      return route.fulfill({ json: { id: 3, question_id: 2, body: "Access is reviewed monthly.", language: "en", gap_notes: "", cited_clause_ids: [4], cited_control_ids: [], suggested_evidence_ids: [], confidence: 0.9, final_body: null, finalized_at: null } });
    });
    await page.goto("/audit");
    await page.getByLabel("Answer body").fill("Access is reviewed monthly.");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => sent).toEqual({ body: "Access is reviewed monthly.", expected_body: "Access is reviewed quarterly." });
  });
});
