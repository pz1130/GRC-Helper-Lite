import { readFileSync } from "node:fs";
import type { Page, Route } from "@playwright/test";

const catalog = JSON.parse(readFileSync(new URL("../../backend/app/rcsa/it_catalog_v1.json", import.meta.url), "utf8"));

// RCSA 浏览器测试共用的 mock：账号、活动、评估项、草稿，以及把 /api/** 全部接管的路由。
export type Handler = (route: Route, url: URL) => Promise<void> | void;

export const LEAD = { id: 1, email: "lead@example.com", name: "Lead", role: "grc_lead" };
export const ASSESSOR = { id: 2, email: "asa@example.com", name: "Asa", role: "contributor" };
export const PEOPLE = [
  { ...LEAD, can_respond: true, can_review: true, can_manage: true, can_accept_risk: true },
  { ...ASSESSOR, can_respond: true, can_review: false, can_manage: false, can_accept_risk: false },
];

export function campaign(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id, name: `Acme Q${id}`, framework_id: 1, framework_snapshot: { key: "acme", name: "Acme IT", version: "1" },
    profile_id: 1, profile_snapshot: { key: "default-5x5", revision: 1, horizon: "12 months", bands: [] },
    period_start: "2026-01-01", period_end: "2026-03-31", due_at: "2026-04-30", timezone: "UTC", owner_id: 1,
    status: "active", status_reason: "", predecessor_campaign_id: null, audit_engagement_id: null,
    activated_at: "2026-01-02T00:00:00Z", closed_at: null, version: 2,
    counts: { total: 2, submitted: 0, approved: 1, excluded: 1 }, ...overrides,
  };
}

export const ITEM = {
  id: 7, campaign_id: 1, control_id: 9,
  control_snapshot: { code: "C-0009", title: "Quarterly access review", statement: "Access is reviewed.", owner_user_id: 2 },
  mapping_snapshot: [{ code: "AC-2", title: "Account management", strength: "full" }], source_snapshot: [],
  assignee_id: 2, reviewer_id: 1, status: "draft", current_response_id: 70, provider_type: "internal", version: 3,
};

export const RESPONSE = {
  id: 70, item_id: 7, revision: 1, status: "draft", answer_schema: 1, risk_snapshot: null,
  submitted_by: null, submitted_at: null, version: 4, evidence: [],
  answer: {
    design: { rating: "effective", rationale: "Covers every account" }, operating: { rating: null, rationale: "" },
    assessment_period_start: null, assessment_period_end: null, limitations: "", independent_test_status: null,
    independent_test_reason: "", not_assessed_reason: "", evidence_followup: null, no_risk_rationale: "",
    evidence_limitations_acknowledged: false,
  },
};

export async function mockApi(page: Page, user: typeof LEAD, routes: Record<string, Handler>, seen: string[] = []) {
  await page.addInitScript(() => localStorage.setItem("grc.token", "mock-token"));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    seen.push(`${route.request().method()} ${url.pathname}`);
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: user });
    if (url.pathname === "/api/proposals/stats") return route.fulfill({ json: { pending: 0, by_kind: {} } });
    if (url.pathname === "/api/health") return route.fulfill({ json: { status: "ok", database: "ok", version: "test" } });
    if (url.pathname === "/api/rcsa/people") return route.fulfill({ json: PEOPLE });
    const handler = routes[url.pathname];
    if (handler) return handler(route, url);
    if (url.pathname === "/api/rcsa/it-catalog") return route.fulfill({ json: catalog });
    if (url.pathname === "/api/rcsa/status") return route.fulfill({ json: { enabled: true } });
    return route.fulfill({ status: 500, json: { message: `Unexpected API: ${url.pathname}` } });
  });
}

export function itemRoutes(patch: Handler): Record<string, Handler> {
  return {
    "/api/rcsa/items/7": (route) => route.fulfill({ json: ITEM }),
    "/api/rcsa/campaigns/1": (route) => route.fulfill({ json: campaign(1) }),
    "/api/rcsa/items/7/responses": (route) => route.fulfill({ json: [RESPONSE] }),
    "/api/rcsa/items/7/reviews": (route) => route.fulfill({ json: [] }),
    "/api/rcsa/items/7/risks": (route) => route.fulfill({ json: [] }),
    "/api/evidence": (route) => route.fulfill({ json: [] }),
    "/api/rcsa/responses/70": patch,
    "/api/rcsa/ai": (route) => route.fulfill({ json: AI_OFF }),
  };
}

export const AI_CHANNEL_OFF = {
  enabled: false, approval_ref: "", allowed_input_types: [], available_input_types: ["control"], provider: null,
};
export const AI_OFF = { prefill: AI_CHANNEL_OFF, evidence_relevance: AI_CHANNEL_OFF };
