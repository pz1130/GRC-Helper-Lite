import { expect, test } from "@playwright/test";
import type { Route } from "@playwright/test";

import { AI_CHANNEL_OFF, ASSESSOR, RESPONSE, itemRoutes, mockApi } from "./rcsa-mocks";

// RCSA 的可选 AI 辅助：全部 mock。服务端的守门（默认关闭、只发允许的输入、不用备用服务、
// 回写前重查）在 backend/tests/test_rcsa_ai.py；这里只看界面说了什么、做了什么。
const PREFILL_ON = {
  prefill: {
    enabled: true, approval_ref: "DPA-7", allowed_input_types: ["control"],
    available_input_types: ["control", "policy_sources", "evidence_metadata"],
    provider: { id: 3, name: "approved-chat", model: "claude-test" },
  },
  evidence_relevance: {
    enabled: true, approval_ref: "DPA-8", allowed_input_types: ["control", "evidence_metadata"],
    available_input_types: ["control", "evidence_metadata"], provider: { id: 4, name: "judge", model: "jev-test" },
  },
};

function suggestion(overrides: Record<string, unknown> = {}) {
  return {
    id: 11, response_id: 70, response_version: 4, stale: false, status: "pending", input_types: ["control"],
    model: "claude-test", prompt_version: "rcsa-prefill-v1", created_at: "2026-02-01T10:00:00Z",
    suggestions: {
      design: { rating: "effective", rationale: "The policy requires a quarterly review", citations: ["control"] },
      operating: { rating: "not_assessed", rationale: "No operating evidence was provided", citations: ["control"] },
      limitations: "Only the control text was available", unknowns: ["Whether reviews happened on time"],
    },
    ...overrides,
  };
}

function routesWith(extra: Record<string, (route: Route) => unknown>) {
  return {
    ...itemRoutes((route) => route.fulfill({ json: RESPONSE })),
    "/api/rcsa/ai": (route: Route) => route.fulfill({ json: PREFILL_ON }),
    ...extra,
  };
}

test("with both channels off there is no AI panel at all", async ({ page }) => {
  await mockApi(page, ASSESSOR, routesWith({
    "/api/rcsa/ai": (route) => route.fulfill({ json: { prefill: AI_CHANNEL_OFF, evidence_relevance: AI_CHANNEL_OFF } }),
  }));
  await page.goto("/rcsa/items/7");
  await expect(page.getByRole("heading", { name: "Answer — revision 1" })).toBeVisible();
  await expect(page.getByText("AI assistance (optional)")).toHaveCount(0);
});

test("the assessor sees what will be sent, then applies only the chosen field", async ({ page }) => {
  let stored: unknown[] = [];
  let applied: unknown = null;
  await mockApi(page, ASSESSOR, routesWith({
    "/api/rcsa/responses/70/suggestions": (route) => route.fulfill({ json: stored }),
    "/api/rcsa/responses/70/prefill": (route) => {
      stored = [suggestion()];
      return route.fulfill({ json: stored[0] });
    },
    "/api/rcsa/suggestions/11/apply": (route) => {
      applied = route.request().postDataJSON();
      return route.fulfill({ json: RESPONSE });
    },
  }));
  await page.goto("/rcsa/items/7");

  const prefill = page.getByRole("region", { name: "Answer prefill" });
  await expect(prefill.getByText("Sends control text to approved-chat (claude-test)")).toBeVisible();
  await prefill.getByRole("button", { name: "Request a suggestion" }).click();
  const card = prefill.getByRole("listitem", { name: "Suggestion 11" });
  await expect(card.getByText("Suggested")).toBeVisible();
  await card.getByRole("checkbox").first().check();
  await card.getByRole("button", { name: "Apply selected fields" }).click();

  await expect.poll(() => applied).toEqual({ fields: ["design"], expected_version: 4 });
});

test("a failed prefill says so and leaves the answer alone", async ({ page }) => {
  await mockApi(page, ASSESSOR, routesWith({
    "/api/rcsa/responses/70/suggestions": (route) => route.fulfill({ json: [] }),
    "/api/rcsa/responses/70/prefill": (route) => route.fulfill({
      status: 502, json: { code: "rcsa.prefill_unavailable", message: "不可用" },
    }),
  }));
  await page.goto("/rcsa/items/7");
  const rationale = page.getByRole("group", { name: "Design effectiveness" }).getByLabel("Rationale");
  await expect(rationale).toHaveValue("Covers every account");

  await page.getByRole("button", { name: "Request a suggestion" }).click();

  await expect(page.getByRole("alert").filter({ hasText: "Prefill unavailable — complete manually" })).toBeVisible();
  await expect(rationale).toHaveValue("Covers every account");
});

test("a stale suggestion is marked and cannot be applied", async ({ page }) => {
  await mockApi(page, ASSESSOR, routesWith({
    "/api/rcsa/responses/70/suggestions": (route) => route.fulfill({ json: [suggestion({ stale: true })] }),
  }));
  await page.goto("/rcsa/items/7");

  const card = page.getByRole("listitem", { name: "Suggestion 11" });
  await expect(card.getByText("Stale — the draft changed since")).toBeVisible();
  await expect(card.getByRole("button", { name: "Apply selected fields" })).toHaveCount(0);
});

test("a failed ranking still lists every candidate for review", async ({ page }) => {
  await mockApi(page, ASSESSOR, routesWith({
    "/api/rcsa/responses/70/suggestions": (route) => route.fulfill({ json: [] }),
    "/api/rcsa/responses/70/rank-evidence": (route) => route.fulfill({ json: {
      ranked: false, reason: "rcsa.ranking_unavailable", model: "jev-test", generated_at: "2026-02-01T10:00:00Z",
      candidates: [
        { evidence_item_id: 1, title: "Q1 review export", evidence_type: "Log", probability: null },
        { evidence_item_id: 2, title: "Old screenshot", evidence_type: "Log", probability: null },
      ],
    } }),
  }));
  await page.goto("/rcsa/items/7");
  await page.getByRole("button", { name: "Rank evidence" }).click();

  await expect(page.getByText("Relevance ranking unavailable — review all candidates")).toBeVisible();
  await expect(page.getByText("Q1 review export")).toBeVisible();
  await expect(page.getByText("Old screenshot")).toBeVisible();
});
