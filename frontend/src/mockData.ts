import { getMockDetail } from "./mockDetail";
// 离线演示模式的 mock 数据：形状必须与真实接口一致，否则页面会在演示里塌掉。

export interface MockUser {
  id: number;
  email: string;
  name: string;
  role: "admin" | "grc_lead" | "contributor" | "viewer";
}

export const DEMO_USER: MockUser = {
  id: 1,
  email: "admin@example.com",
  name: "GRC Demo",
  role: "admin",
};

export const DEMO_TOKEN = "demo-mock-token";

export function getMockResponse(path: string, _init: RequestInit = {}): unknown {
  const url = path.split("?")[0];
  const detail = getMockDetail(url, _init);
  if (detail !== undefined) return detail;

  // 离线演示没有后端可问。返回 ok 是为了不在演示里挂一盏假的红灯——
  // 演示模式本来就整套是示例数据，这里跟着示例走是一致的，不是在骗人。
  if (url === "/api/health") {
    return { status: "ok", database: "ok", version: "demo" };
  }

  if (url === "/api/auth/me") {
    return DEMO_USER;
  }

  if (url === "/api/auth/login") {
    return {
      access_token: DEMO_TOKEN,
      user: DEMO_USER,
    };
  }

  if (url === "/api/settings/usage") {
    return {
      month_to_date_cost: 142.8,
      budget: 500.0,
      by_task: [
        { task_key: "clause_extraction", cost: 72.4, calls: 1420 },
        { task_key: "framework_mapping", cost: 48.2, calls: 980 },
        { task_key: "gap_analysis", cost: 22.2, calls: 360 },
      ],
    };
  }

  if (url === "/api/evidence/stats") {
    return { expired: 3 };
  }

  if (url === "/api/proposals/stats") {
    return {
      pending: 4,
      by_kind: { extraction: 2, mapping: 2 },
    };
  }

  if (url === "/api/documents") {
    return [
      {
        id: 1,
        title: "Enterprise Information Security Policy",
        doc_type: "policy",
        status: "active",
        version: "v3.2",
        owner: "CISO Office",
        effective_date: "2024-01-01",
        review_due_date: "2026-09-01",
        original_filename: "security_policy_2024.docx",
        parse_error: null,
        parse_warnings: null,
        parse_warning_codes: null,
        ocr_quality_flag: false,
      },
      {
        id: 2,
        title: "Identity and Access Control Standard",
        doc_type: "standard",
        status: "active",
        version: "v2.1",
        owner: "IAM Team",
        effective_date: "2024-06-01",
        review_due_date: "2026-10-15",
        original_filename: "iam_access_spec.pdf",
        parse_error: null,
        parse_warnings: null,
        parse_warning_codes: null,
        ocr_quality_flag: false,
      },
      {
        id: 3,
        title: "Cross-border Data Transfer and DLP Guideline",
        doc_type: "guideline",
        status: "draft",
        version: "v1.4",
        owner: "Compliance Legal",
        effective_date: "2025-01-15",
        review_due_date: "2026-11-20",
        original_filename: "cross_border_data_transfer.pdf",
        parse_error: null,
        parse_warnings: null,
        parse_warning_codes: null,
        ocr_quality_flag: false,
      },
      {
        id: 4,
        title: "Production Privileged Account Lifecycle Procedure",
        doc_type: "procedure",
        status: "active",
        version: "v2.0",
        owner: "DevOps Ops",
        effective_date: "2024-09-01",
        review_due_date: "2026-12-10",
        original_filename: "privileged_account_lifecycle.docx",
        parse_error: null,
        parse_warnings: null,
        parse_warning_codes: null,
        ocr_quality_flag: false,
      },
    ];
  }

  if (url === "/api/documents/coverage") {
    return [
      { document_id: 1, clauses: 42, normative_clauses: 36, controls: 32, proposals: 4, uncovered_normative: 4, never_extracted: false },
      { document_id: 2, clauses: 28, normative_clauses: 24, controls: 22, proposals: 2, uncovered_normative: 2, never_extracted: false },
      { document_id: 3, clauses: 15, normative_clauses: 12, controls: 0, proposals: 0, uncovered_normative: 12, never_extracted: true },
      { document_id: 4, clauses: 19, normative_clauses: 18, controls: 18, proposals: 1, uncovered_normative: 0, never_extracted: false },
    ];
  }

  if (url === "/api/frameworks") {
    // 字段名必须跟 FrameworkOut 走：key / name_zh / name_en / source / item_count。
    // 这里曾经用 code / name / publisher / total_controls，于是名称列整列空、
    // "条目数"只剩标签（缺失的字段 React 直接渲染成空，连 undefined 都不留），
    // 成熟度的框架下拉也只剩 "· 2.0"。
    return [
      { id: 1, key: "nist-csf-2.0", name_zh: "NIST 网络安全框架 2.0", name_en: "NIST Cybersecurity Framework 2.0", version: "2.0", source: "nist.gov", item_count: 134, imported_at: "2026-09-19T09:00:00Z" },
      { id: 2, key: "nist-800-53-r5", name_zh: "NIST SP 800-53 Rev.5", name_en: "NIST SP 800-53 Rev.5", version: "5.1.1", source: "nist.gov", item_count: 1034, imported_at: "2026-09-19T09:05:00Z" },
      { id: 3, key: "iso-27001-2022", name_zh: "ISO/IEC 27001:2022 信息安全管理体系", name_en: "ISO/IEC 27001:2022 Information Security Management Systems", version: "2022", source: "iso.org", item_count: 93, imported_at: "2026-09-19T09:10:00Z" },
    ];
  }

  if (url === "/api/controls") {
    return [
      { id: 1, code: "AC-1", title: "Access Control Policy and Procedures", category: "Access Control", status: "implemented", statement: "Establish, distribute and review organization-wide access control policies and procedures." },
      { id: 2, code: "IA-2", title: "Identification and Authentication", category: "Identification & Authentication", status: "in_progress", statement: "Require phishing-resistant multifactor authentication for internal network and privileged account access." },
      { id: 3, code: "SC-7", title: "Boundary Protection and Microsegmentation", category: "System & Communications", status: "implemented", statement: "Apply zero-trust microsegmentation and deep packet inspection between critical business systems." },
      { id: 4, code: "SI-4", title: "System Monitoring and Intrusion Detection", category: "System Integrity", status: "implemented", statement: "Continuously collect production system logs and audit trails and route them to the SIEM alerting center." },
      { id: 5, code: "CP-9", title: "System Backup", category: "Contingency Planning", status: "in_progress", statement: "Back up core banking data daily and test restoration at least twice a year." },
      { id: 6, code: "CM-3", title: "Configuration Change Control", category: "Configuration Management", status: "implemented", statement: "Route production changes through CAB approval with recorded rollback plans." },
      { id: 7, code: "IR-4", title: "Incident Handling", category: "Incident Response", status: "implemented", statement: "Maintain an incident handling capability covering preparation, detection, containment and recovery." },
      { id: 8, code: "SA-9", title: "External System Services", category: "System & Services Acquisition", status: "in_progress", statement: "Require third-party service providers to meet organizational security requirements and provide annual assurance." },
    ];
  }

  if (url === "/api/tech-assets") {
    // category / environment / status 都是后端枚举，**小写**，语言包里 11+4+3
    // 个键全都齐（不是"缺翻译"）。这里曾经写 "Network"/"Identity"/"Compute"——
    // 首字母大写、且后两个根本不在枚举里，于是界面上出现
    // techAssets.categories.Identity 这样的原始键；environment 干脆没给。
    return [
      { id: 1, name: "AWS Production Cloud VPC (ap-east-1)", category: "cloud", vendor: "AWS", environment: "prod", owner_user_id: 1, scope_note: "Production VPC hosting core workloads", status: "active", created_at: "2026-01-10T00:00:00Z" },
      { id: 2, name: "Core Financial DB Cluster (PostgreSQL + pgvector)", category: "database", vendor: "PostgreSQL", environment: "prod", owner_user_id: 1, scope_note: "Core finance database, contains customer PII", status: "active", created_at: "2026-01-12T00:00:00Z" },
      { id: 3, name: "SSO & IAM Identity Provider (Keycloak)", category: "iam", vendor: "Keycloak", environment: "all", owner_user_id: 1, scope_note: "Bank-wide single sign-on", status: "active", created_at: "2026-02-01T00:00:00Z" },
      { id: 4, name: "Kubernetes Enterprise Workload Cluster", category: "other", vendor: "Red Hat", environment: "dr", owner_user_id: null, scope_note: "DR cluster, exercised quarterly", status: "planned", created_at: "2026-03-08T00:00:00Z" },
    ];
  }

  if (url === "/api/evidence") {
    // 真接口是 EvidenceItemOut。三个字段曾经漏掉或写错，各自造一种泄漏：
    //   · 没有 display_status → t("evidence.statuses.undefined") 吐出原始键
    //   · expires_at 不存在（真名 valid_until）→ 有效期列空
    //   · status 直接写 "valid"/"expired" → 那是**算出来的展示态**，不是存储态；
    //     存储态只有 planned/collected/missing（app/evidence/models.py）。
    return [
      { id: 1, evidence_type_id: 1, control_id: 1, tech_asset_id: 1, title: "2026 Q3 Bastion Host Audit and Session Replay", owner_user_id: 1, location_hint: "VaultKeeper › Reports", last_collected_at: "2026-07-05T02:00:00Z", valid_until: "2026-12-31T00:00:00Z", file_path: null, status: "collected", intent_status: "collected", display_status: "valid", created_at: "2026-07-05T02:05:00Z", evidence_type_name: "Review report", control_code: "AC-1", control_title: "Access Control Policy and Procedures", tech_asset_name: "VaultKeeper" },
      { id: 2, evidence_type_id: 2, control_id: 3, tech_asset_id: 2, title: "Production Database SSL Wildcard Certificate Rotation", owner_user_id: 1, location_hint: "ServiceNow › CHG-8821", last_collected_at: "2025-08-15T00:00:00Z", valid_until: "2026-08-15T00:00:00Z", file_path: null, status: "collected", intent_status: "collected", display_status: "expired", created_at: "2025-08-15T01:00:00Z", evidence_type_name: "Change ticket", control_code: "SC-7", control_title: "Boundary Protection and Microsegmentation", tech_asset_name: "ServiceNow" },
      { id: 3, evidence_type_id: 3, control_id: 4, tech_asset_id: null, title: "Third-party Penetration Test Report (2026)", owner_user_id: null, location_hint: "", last_collected_at: null, valid_until: null, file_path: null, status: "planned", intent_status: "planned", display_status: "planned", created_at: "2026-09-01T03:00:00Z", evidence_type_name: "Assessment report", control_code: "SI-4", control_title: "System Monitoring and Intrusion Detection", tech_asset_name: null },
    ];
  }

  if (url === "/api/proposals") {
    // 形状必须跟 ProposalOut 走：ReviewQueue 读的是 payload / citations /
    // bulk_acceptable 这些。这里曾经只给 {source_text, target_framework}，
    // 于是 `proposal.payload.framework_item` 直接抛（BUG-02 白屏）。
    return [
      {
        id: 101, kind: "mapping", status: "pending", confidence: 0.94,
        document_id: 1, decided_by: null, decided_at: null, decided_payload: null,
        reject_reason: null, created_at: "2026-09-18T09:20:00Z",
        bulk_acceptable: true, ocr_quality_flag: false, normative_drift: false,
        citations: [],
        payload: {
          control_id: 1, framework_item_id: 11, strength: "full",
          control: { id: 1, code: "AC-1", title: "Access Control Policy and Procedures", statement: "Clause 4.2: Production data-center access requires dual approval and an audit trail." },
          framework_item: { id: 11, code: "PR.AA-01", title: "Identities and credentials are managed", description: "" },
          framework_item_quote: "Identities and credentials are issued, managed, verified and revoked.",
          rationale: "Dual approval maps to separation of duties for credential issuance.",
        },
      },
      {
        id: 102, kind: "extraction", status: "pending", confidence: 0.89,
        document_id: 1, decided_by: null, decided_at: null, decided_payload: null,
        reject_reason: null, created_at: "2026-09-18T09:22:00Z",
        bulk_acceptable: false, ocr_quality_flag: false, normative_drift: false,
        citations: [],
        payload: {
          code: "SC-13", title: "Encryption in transit and at rest",
          statement: "Clause 7.1: Data at rest and in transit must use AES-256 or equivalent encryption.",
          category: "System & Communications",
        },
      },
    ];
  }

  if (url === "/api/settings/providers") {
    return [
      { id: 1, name: "OpenAI Production", base_url: "https://api.openai.com/v1", model: "gpt-4o", masked_key: "sk-proj-****89Ab", is_active: true },
      { id: 2, name: "Anthropic Claude Dedicated", base_url: "https://api.anthropic.com/v1", model: "claude-3-5-sonnet", masked_key: "sk-ant-****55Ff", is_active: true },
    ];
  }

  if (url === "/api/settings/users") {
    return [
      { id: 1, email: "admin@example.com", name: "Admin (Demo)", role: "admin", is_active: true },
      { id: 2, email: "lead@example.com", name: "GRC Lead", role: "grc_lead", is_active: true },
      { id: 3, email: "auditor@example.com", name: "External Auditor", role: "viewer", is_active: true },
    ];
  }

  if (url === "/api/settings/redaction") {
    // 真实 API 返回的是 list[RedactionRuleOut]——**数组**。这里曾经返回
    // `{enabled, rules}` 对象，于是 Redaction.tsx 的 `data?.filter(...)` 直接
    // 抛 "b.filter is not a function"（BUG-16）。mock 的形状必须跟着真接口走。
    return [
      { id: 1, ruleset: "generation", kind: "chinese_id_card", pattern: "\\d{17}[\\dXx]", replacement: "[ID]", enabled: true },
      { id: 2, ruleset: "generation", kind: "phone_number", pattern: "1[3-9]\\d{9}", replacement: "[PHONE]", enabled: true },
      { id: 3, ruleset: "indexing", kind: "email", pattern: "\\S+@\\S+", replacement: "[EMAIL]", enabled: true },
    ];
  }

  if (url === "/api/settings/thresholds") {
    return { min_confidence: 0.85, require_human_review: true, high_risk_auto_escalate: true };
  }

  if (url === "/api/settings/audit-log") {
    return [
      { id: 1, actor: "admin@example.com", action: "UPDATE_LLM_CONFIG", target: "Provider: OpenAI Production", timestamp: "2026-09-17T18:30:00Z" },
      { id: 2, actor: "system", action: "SCHEDULED_EVIDENCE_SWEEP", target: "Evidence #2 marked as EXPIRED", timestamp: "2026-09-17T00:00:00Z" },
    ];
  }

  if (url === "/api/search") {
    // 真实 API 返回 SearchResponse **对象**（query/expanded_terms/hits/vector_used）。
    // 这里曾经返回 []，于是 Search.tsx 读 `.hits.length` 直接抛（BUG-04：
    // 检索页本身正常，一提交就白屏）。
    return {
      query: "",
      expanded_terms: [],
      vector_used: false,
      vector_unavailable_reason: "provider_unconfigured",
      hits: [
        {
          chunk_id: 1, clause_id: 42, document_id: 1,
          document_title: "Information Security Policy",
          citation_label: "4.2", heading_path: "4 Access Control › 4.2 Data-center access",
          text: "Production data-center access requires dual approval and an audit trail.",
          score: 0.82, rank_fulltext: 1, rank_vector: null,
        },
      ],
    };
  }

  // 风险登记册的演示数据：形状对齐 RiskRegister.tsx 的 Risk / Owner，
  // 分数覆盖热力图的高、中、低三档，否则演示里矩阵只有一格亮。
  if (url === "/api/risks/owners") {
    return [
      { id: 1, name: "GRC Demo", email: "admin@example.com" },
      { id: 2, name: "Dana Lee", email: "dana@acme.example" },
      { id: 3, name: "Omar Haddad", email: "omar@acme.example" },
    ];
  }

  if (url === "/api/risks") {
    const r = (id: number, title: string, source: string, l: number, i: number, rl: number | null, ri: number | null, owner: number | null, due: string | null, status: string, mitigation: string) => ({
      id, title, description: "", source, source_ref: {}, likelihood: l, impact: i, inherent_score: l * i, mitigation,
      residual_likelihood: rl, residual_impact: ri, residual_score: rl && ri ? rl * ri : null, owner_user_id: owner, due_date: due, status,
    });
    return [
      r(1, "Privileged access not reviewed quarterly", "gap", 4, 5, 2, 4, 2, "2026-11-15", "mitigating", "Automate quarterly entitlement review in IAM."),
      r(2, "Backup restore never tested for core ledger", "audit_finding", 3, 5, null, null, 3, "2026-10-30", "open", ""),
      r(3, "Conflicting password rotation clauses", "conflict", 3, 3, 1, 3, 1, "2026-12-01", "mitigating", "Align policy 4.3 with standard 2.1."),
      r(4, "Vendor SOC report expired", "manual", 2, 4, 2, 2, 2, null, "accepted", "Compensating control: quarterly vendor attestation."),
      r(5, "MFA gaps on legacy VPN", "gap", 4, 4, null, null, null, "2026-10-20", "open", ""),
      r(6, "Log retention below 12 months", "gap", 2, 3, 1, 2, 3, "2026-09-30", "closed", "SIEM retention raised to 400 days."),
    ];
  }

  // 没覆盖的路径返回 undefined，由 api.ts 转成一个**被处理的**错误。
  // 这里曾经兜底 `return []`：任何没列进来的接口都拿到一个空数组，而页面等的
  // 往往是对象——`[].overall.total_items`、`{}.filter(...)` 就是这么炸的
  // （2026-09-20 上线前测试里 6 个白屏中的 5 个）。喂一个形状错误的值，比
  // 明说"演示数据没覆盖这里"危险得多：前者变成崩溃，后者只是一块空态。
  return undefined;
}
