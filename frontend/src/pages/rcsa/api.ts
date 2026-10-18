// RCSA 接口的类型与调用。形状与后端 app/rcsa/schemas.py 一一对应。
import { ApiError, request } from "../../api";

export type CampaignStatus = "draft" | "active" | "in_review" | "closed" | "cancelled";
export type ItemStatus = "pending" | "draft" | "submitted" | "returned" | "approved" | "excluded";
export type Effectiveness = "effective" | "partially_effective" | "ineffective" | "not_assessed";
export type TestStatus = "provided" | "not_provided" | "not_applicable";
export type Disposition = "accept" | "reduce" | "transfer" | "avoid" | "terminate";

export interface Page<T> {
  items: T[];
  total: number;
}

export interface Person {
  id: number;
  name: string;
  email: string;
  role: string;
  can_respond: boolean;
  can_review: boolean;
  can_manage: boolean;
  can_accept_risk: boolean;
}

export interface Band {
  min: number;
  max: number;
  label: string;
}

export interface Profile {
  id: number;
  key: string;
  revision: number;
  algorithm: string;
  definitions: {
    likelihood?: { level: number; label: string; criteria: string }[];
    impact?: { level: number; label: string; criteria: Record<string, string> }[];
    impact_dimensions?: string[];
    impact_rule?: string;
  };
  bands: Band[];
  horizon: string;
  status: "draft" | "approved";
  approved_by: number | null;
  approved_at: string | null;
  version: number;
}

export interface Campaign {
  id: number;
  name: string;
  framework_id: number;
  framework_snapshot: { key: string; name: string; version: string } | null;
  profile_id: number;
  profile_snapshot: { key: string; revision: number; horizon: string; bands: Band[] } | null;
  period_start: string;
  period_end: string;
  due_at: string;
  timezone: string;
  owner_id: number;
  status: CampaignStatus;
  status_reason: string;
  activated_at: string | null;
  closed_at: string | null;
  version: number;
  counts: { total: number; submitted: number; approved: number; excluded: number };
}

export interface ScopeRow {
  control_id: number;
  code: string;
  title: string;
  owner_user_id: number | null;
  framework_items: string[];
  included: boolean;
}

export interface Item {
  id: number;
  campaign_id: number;
  control_id: number;
  control_snapshot: { code: string; title: string; statement: string; owner_user_id: number | null } | null;
  mapping_snapshot: { code: string; title: string; strength: string }[] | null;
  source_snapshot: { citation_label: string; document_id: number; relation: string }[] | null;
  assignee_id: number | null;
  reviewer_id: number | null;
  status: ItemStatus;
  current_response_id: number | null;
  version: number;
}

export interface Rating {
  rating: Effectiveness | null;
  rationale: string;
}

export interface Answer {
  design: Rating;
  operating: Rating;
  assessment_period_start: string | null;
  assessment_period_end: string | null;
  limitations: string;
  independent_test_status: TestStatus | null;
  independent_test_reason: string;
  not_assessed_reason: string;
  evidence_followup: { owner_id: number; due_at: string; description: string } | null;
  no_risk_rationale: string;
  evidence_limitations_acknowledged: boolean;
}

export interface EvidenceRef {
  id: number;
  evidence_item_id: number | null;
  external_location: string | null;
  purpose: "self_assessment" | "independent_test";
  snapshot: Record<string, string | null>;
  tester_user_id: number | null;
  tester_name: string | null;
  tested_at: string | null;
  test_scope: string;
  conclusion: string;
  independence_basis: string;
}

export interface Response {
  id: number;
  item_id: number;
  revision: number;
  status: "draft" | "submitted" | "approved" | "returned";
  answer: Answer;
  submitted_by: number | null;
  submitted_at: string | null;
  version: number;
  evidence: EvidenceRef[];
}

export interface Review {
  id: number;
  decision: string;
  target_version: number;
  reviewer_id: number;
  reason: string;
  at: string;
}

export interface Risk {
  id: number;
  item_id: number;
  scenario_key: string;
  register_risk_id: number | null;
  cause: string;
  event: string;
  impact: string;
  inherent_likelihood: number;
  inherent_impact: number;
  inherent_score: number;
  inherent_rationale: string;
  residual_likelihood: number | null;
  residual_impact: number | null;
  residual_score: number | null;
  residual_rationale: string;
  owner_id: number;
  disposition: Disposition | null;
  disposition_status: "pending" | "proposed" | "approved" | "returned";
  disposition_revision: number;
  disposition_proposed_by: number | null;
  accept_until: string | null;
  reassess_at: string | null;
  disposition_details: Record<string, string | boolean>;
  version: number;
}

export interface Action {
  id: number;
  item_risk_id: number;
  title: string;
  root_cause: string;
  measure: string;
  owner_id: number;
  verifier_id: number;
  due_at: string;
  success_criteria: string;
  status: "open" | "in_progress" | "pending_verification" | "verified" | "cancelled";
  implementation_evidence: { description: string; location: string }[];
  verification_result: string;
  verified_at: string | null;
  version: number;
}

export interface Trigger {
  id: number;
  trigger_type: "planned_change" | "incident" | "other";
  title: string;
  description: string;
  source_ref: string;
  occurred_at: string;
  planned_effective_at: string | null;
  owner_id: number;
  decision_due_at: string;
  status: "registered" | "triaged" | "awaiting_assessment" | "no_reassessment" | "completed";
  disposition: "reassess" | "no_reassessment" | null;
  proposed_by: number | null;
  rationale: string;
  campaign_id: number | null;
  version: number;
  controls: {
    id: number;
    control_id: number;
    control_snapshot: { code: string; title: string };
    coverage_disposition: "pending" | "assess" | "excluded";
    coverage_reason: string;
  }[];
}

export type WorkKind =
  | "assessment" | "review" | "disposition" | "approval" | "acceptance" | "action" | "verification"
  | "trigger" | "trigger_review";

export interface WorkRow {
  kind: WorkKind;
  id: number;
  title: string;
  item_id: number | null;
  campaign_id: number | null;
  campaign_name: string | null;
  status: string;
  due_at: string | null;
  overdue: boolean;
}

export interface Blocker {
  code: string;
  item_id?: number;
  risk_id?: number;
  control_code?: string;
  count?: number;
}

/** 一次用户动作一把幂等键：重试同一动作用同一把，换一次点击就换一把。 */
export function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function send<T>(path: string, method: string, body: unknown, key?: string): Promise<T> {
  return request<T>(path, {
    method,
    body: JSON.stringify(body),
    headers: key ? { "Idempotency-Key": key } : undefined,
  });
}

/** 错误里附带的清单（阻断项或缺项），没有就是空数组。 */
export function listedIn(error: unknown, field: "blockers" | "problems"): Blocker[] {
  if (!(error instanceof ApiError) || !error.params) return [];
  const list = error.params[field];
  if (!Array.isArray(list)) return [];
  return list.map((entry) => (typeof entry === "string" ? { code: entry } : (entry as Blocker)));
}

export function isConflict(error: unknown): boolean {
  return error instanceof ApiError && error.code === "rcsa.version_conflict";
}

export type InputType = "control" | "policy_sources" | "evidence_metadata";

export interface AiChannel {
  enabled: boolean;
  approval_ref: string;
  allowed_input_types: InputType[];
  available_input_types: InputType[];
  provider: { id: number; name: string; model: string } | null;
}

export interface AiStatus {
  prefill: AiChannel;
  evidence_relevance: AiChannel;
}

export interface SuggestedPart {
  rating: Effectiveness | null;
  rationale: string;
  citations: string[];
}

export interface Suggestion {
  id: number;
  response_id: number;
  response_version: number;
  stale: boolean;
  status: string;
  suggestions: { design: SuggestedPart; operating: SuggestedPart; limitations: string; unknowns: string[] };
  input_types: InputType[];
  model: string | null;
  prompt_version: string;
  created_at: string;
}

export interface Ranking {
  ranked: boolean;
  reason: string | null;
  model: string | null;
  generated_at: string;
  candidates: { evidence_item_id: number; title: string; evidence_type: string | null; probability: number | null }[];
}

export const api = {
  ai: () => request<AiStatus>("/api/rcsa/ai"),
  suggestions: (responseId: number) => request<Suggestion[]>(`/api/rcsa/responses/${responseId}/suggestions`),
  status: () => request<{ enabled: boolean }>("/api/rcsa/status"),
  people: () => request<Person[]>("/api/rcsa/people"),
  profiles: () => request<Profile[]>("/api/rcsa/profiles"),
  campaigns: (status: string, offset: number, limit: number) =>
    request<Page<Campaign>>(
      `/api/rcsa/campaigns?limit=${limit}&offset=${offset}${status ? `&status=${status}` : ""}`,
    ),
  campaign: (id: number) => request<Campaign>(`/api/rcsa/campaigns/${id}`),
  scope: (id: number) => request<ScopeRow[]>(`/api/rcsa/campaigns/${id}/scope`),
  items: (id: number) => request<Item[]>(`/api/rcsa/campaigns/${id}/items`),
  item: (id: number) => request<Item>(`/api/rcsa/items/${id}`),
  responses: (itemId: number) => request<Response[]>(`/api/rcsa/items/${itemId}/responses`),
  reviews: (itemId: number) => request<Review[]>(`/api/rcsa/items/${itemId}/reviews`),
  risks: (itemId: number) => request<Risk[]>(`/api/rcsa/items/${itemId}/risks`),
  riskReviews: (riskId: number) => request<Review[]>(`/api/rcsa/item-risks/${riskId}/reviews`),
  actions: (riskId: number) => request<Action[]>(`/api/rcsa/item-risks/${riskId}/actions`),
  work: () => request<{ rows: WorkRow[] }>("/api/rcsa/my-work"),
  triggers: (offset: number, limit: number) =>
    request<Page<Trigger>>(`/api/rcsa/triggers?limit=${limit}&offset=${offset}`),
  trigger: (id: number) => request<Trigger>(`/api/rcsa/triggers/${id}`),
  report: (id: number) => request<Report>(`/api/rcsa/campaigns/${id}/report`),
};

export interface Report {
  watermark: "draft" | "cancelled" | null;
  source: "closed_snapshot" | "live";
  generated_at: string;
  campaign: {
    id: number;
    name: string;
    status: CampaignStatus;
    period_start: string;
    period_end: string;
    due_at: string;
    closed_at: string | null;
    framework: { key: string; name: string; version: string } | null;
    profile: { key: string | null; revision: number | null; horizon: string | null; bands: Band[] | null };
  };
  scope: { total: number; approved: number; excluded: number; submitted: number; in_progress: number };
  exclusions: { control_code: string; reason: string }[];
  not_assessed: { control_code: string; ratings: string[]; reason: string; follow_up_due: string | null }[];
  independent_tests: Record<string, number>;
  scenario_bands: { inherent: Record<string, number>; residual: Record<string, number> };
  dispositions: Record<string, number>;
  acceptances: { control_code: string; scenario_key: string; accept_until: string; expired: boolean }[];
  actions: { open: number; overdue: number; verified: number; cancelled: number };
  limitations: { control_code: string; text: string }[];
  acknowledgements: { overdue: string; evidence: string } | null;
  highest_residual_band: string | null;
  ai_assistance: "not_enabled" | "enabled";
  comparison: {
    previous: { id: number; name: string; status: CampaignStatus };
    comparable: boolean;
    reason: "previous_not_closed" | "profile_changed" | null;
    scenarios: {
      control_code: string;
      scenario_key: string;
      status: "comparable" | "not_directly_comparable" | "new" | "removed";
      previous_residual_score: number | null;
      previous_residual_band: string | null;
      current_residual_score: number | null;
      current_residual_band: string | null;
    }[];
  } | null;
  items: {
    item_id: number;
    control_code: string;
    control_title: string;
    status: ItemStatus;
    design: Effectiveness | null;
    operating: Effectiveness | null;
    independent_test_status: TestStatus | null;
    scenarios: {
      scenario_key: string;
      event: string;
      inherent_score: number;
      inherent_band: string;
      residual_score: number | null;
      residual_band: string;
      disposition: Disposition | null;
      disposition_status: string;
    }[];
  }[];
  current_follow_up: {
    actions: { id: number; title: string; control_code: string; status: string; due_at: string; overdue: boolean }[];
    acceptances: { risk_id: number; control_code: string; accept_until: string; expired: boolean }[];
  };
}
