import { expect, test } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";

// 真实后端的 RCSA 全流程，数据来自 `make e2e` 里的 tests.e2e_rcsa_seed（合成的 Acme）。
// 三个人各开一个浏览器上下文：负责人建活动、作答人作答、复核人复核，负责人关闭。
const PASSWORD = "pw123456";
const LEAD = "rcsa-lead@example.com";
const ASSESSOR = "rcsa-assessor@example.com";
const REVIEWER = "rcsa-reviewer@example.com";
const CAMPAIGN = `Acme RCSA ${Date.now()}`;

async function as(browser: Browser, email: string): Promise<Page> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Compliance & Governance Cockpit" })).toBeVisible();
  return page;
}

function watchModelCalls(page: Page): string[] {
  const calls: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (/\/api\/llm|prefill|rank-evidence/.test(path)) calls.push(path);
  });
  return calls;
}

test("a campaign runs from activation to a closed report with three people", async ({ browser }) => {
  test.setTimeout(180_000);

  // ── 负责人：建草稿、选范围、指定复核人、激活 ──
  const lead = await as(browser, LEAD);
  const leadCalls = watchModelCalls(lead);
  await lead.getByRole("link", { name: "RCSA" }).click();
  await lead.getByRole("link", { name: "New campaign" }).click();
  await lead.getByLabel("Name").fill(CAMPAIGN);
  await lead.getByLabel("Period start").fill("2026-01-01");
  await lead.getByLabel("Period end").fill("2026-03-31");
  await lead.getByLabel("Due date").fill("2099-04-30");
  await lead.getByRole("button", { name: "Next" }).click();
  await lead.getByRole("combobox", { name: "Framework", exact: true }).selectOption({ label: "Acme IT Controls 1" });
  await lead.getByRole("combobox", { name: "Scoring profile", exact: true }).selectOption({ label: "default-5x5 v1 — Approved" });
  await lead.getByRole("button", { name: "Create draft" }).click();
  await expect(lead.getByRole("heading", { name: CAMPAIGN })).toBeVisible();

  for (const box of await lead.getByRole("checkbox", { name: /^Add C-/ }).all()) await box.check();
  await lead.getByLabel("Reviewer for the added controls").selectOption({ label: "Remy Reviewer" });
  await lead.getByRole("button", { name: "Add 2 to scope" }).click();
  await expect(lead.getByRole("cell", { name: /Quarterly access review/ })).toBeVisible();
  await lead.getByRole("button", { name: "Check and activate" }).click();
  await expect(lead.getByText("Active", { exact: true }).first()).toBeVisible();
  const campaignUrl = lead.url();

  // ── 作答人：作答、保存、刷新不丢、提交；看不到复核按钮 ──
  const assessor = await as(browser, ASSESSOR);
  const assessorCalls = watchModelCalls(assessor);
  await assessor.goto("/rcsa");
  await assessor.getByRole("region", { name: "My assessments" }).getByRole("listitem")
    .filter({ hasText: CAMPAIGN }).getByRole("link", { name: /Quarterly access review/ }).click();
  await assessor.getByRole("button", { name: "Start assessment" }).click();
  const design = assessor.getByRole("group", { name: "Design effectiveness" });
  await design.getByLabel("Rating").selectOption("effective");
  await design.getByLabel("Rationale").fill("Every privileged account is on the review list");
  const operating = assessor.getByRole("group", { name: "Operating effectiveness" });
  await operating.getByLabel("Rating").selectOption("partially_effective");
  await operating.getByLabel("Rationale").fill("Two reviews closed late");
  const period = assessor.getByRole("group", { name: "Assessment period" });
  await period.getByLabel("Period start").fill("2026-01-01");
  await period.getByLabel("Period end").fill("2026-03-31");
  await assessor.getByRole("group", { name: "Independent test" }).getByLabel("Independent test").selectOption("not_provided");
  await assessor.getByLabel("Why there is none").fill("No internal audit coverage this quarter");
  await assessor.getByLabel("If there is no risk scenario, explain why").fill("Exposure is within the accepted baseline");
  await assessor.getByRole("button", { name: "Save" }).click();
  await expect(assessor.getByRole("status").filter({ hasText: "Saved" })).toBeVisible();

  await assessor.reload();
  await expect(assessor.getByRole("group", { name: "Design effectiveness" }).getByLabel("Rationale"))
    .toHaveValue("Every privileged account is on the review list");
  await expect(assessor.getByRole("button", { name: /Approve revision/ })).toHaveCount(0);
  await assessor.getByRole("button", { name: "Submit for review" }).click();
  await expect(assessor.getByText("Submitted").first()).toBeVisible();

  // ── 复核人：批准第一项，有理由地排除第二项 ──
  const reviewer = await as(browser, REVIEWER);
  const reviewerCalls = watchModelCalls(reviewer);
  await reviewer.goto("/rcsa");
  await reviewer.getByRole("region", { name: "Reviews" }).getByRole("listitem")
    .filter({ hasText: CAMPAIGN }).getByRole("link", { name: /Quarterly access review/ }).click();
  await reviewer.getByRole("button", { name: "Approve revision 1" }).click();
  await expect(reviewer.getByText("Approved").first()).toBeVisible();

  await reviewer.goto(campaignUrl);
  await reviewer.getByRole("link", { name: /^C-/ }).nth(1).click();
  await expect(reviewer.getByRole("heading", { name: /Backup restore test/ })).toBeVisible();
  await reviewer.getByLabel("Reason").fill("Backup platform decommissioned in January");
  await reviewer.getByRole("button", { name: "Exclude item" }).click();
  await expect(reviewer.getByText("Excluded").first()).toBeVisible();

  // ── 负责人：进入复核、关闭、看报告 ──
  await lead.goto(campaignUrl);
  await lead.getByRole("button", { name: "Begin review" }).click();
  await expect(lead.getByText("In review").first()).toBeVisible();
  await lead.getByRole("button", { name: "Close campaign" }).click();
  await expect(lead.getByText("Closed").first()).toBeVisible();
  await lead.getByRole("link", { name: "Report" }).click();
  await expect(lead.getByText("Figures are as frozen at closing")).toBeVisible();
  await expect(lead.getByText("Backup platform decommissioned in January")).toBeVisible();
  await expect(lead.getByRole("note")).toHaveCount(0);

  // 没有配置 AI：整个人工流程不应有任何模型调用。
  expect([...leadCalls, ...assessorCalls, ...reviewerCalls]).toEqual([]);
});
