// 离线演示模式的第二批 mock：控制点详情、框架详情（条款树 / 覆盖 / 缺口）、
// 成熟度评估与知识图谱。形状对齐各页面里的接口类型——对不上的话演示会白屏，
// 那比显示「演示数据没覆盖这里」更糟。只回答 GET：写操作在演示里仍应明说不支持。

const CONTROLS: Record<number, { code: string; title: string; category: string; statement: string }> = {
  1: { code: "AC-1", title: "Access Control Policy and Procedures", category: "Access Control", statement: "Establish, distribute and review organization-wide access control policies and procedures." },
  2: { code: "IA-2", title: "Identification and Authentication", category: "Identification & Authentication", statement: "Require phishing-resistant multifactor authentication for internal network and privileged account access." },
  3: { code: "SC-7", title: "Boundary Protection and Microsegmentation", category: "System & Communications", statement: "Apply zero-trust microsegmentation and deep packet inspection between critical business systems." },
  4: { code: "SI-4", title: "System Monitoring and Intrusion Detection", category: "System Integrity", statement: "Continuously collect production system logs and audit trails and route them to the SIEM alerting center." },
};

function controlDetail(id: number) {
  const c = CONTROLS[id] ?? CONTROLS[1];
  return {
    id, ...c, status: "implemented",
    sources: [
      { clause_id: 42, document_id: 1, document_title: "Information Security Policy", citation_label: "4.2", heading_path: "4 Access Control › 4.2 Data-center access", relation: "derived" },
      { clause_id: 57, document_id: 2, document_title: "Identity Management Standard", citation_label: "2.1", heading_path: "2 Accounts › 2.1 Provisioning", relation: "supporting" },
    ],
    relations: [
      { from_control_id: id, to_control_id: 2, from_control_code: c.code, to_control_code: "IA-2", relation_type: "depends_on", rationale: "Policy relies on strong authentication to enforce least privilege." },
      { from_control_id: 4, to_control_id: id, from_control_code: "SI-4", to_control_code: c.code, relation_type: "monitors", rationale: "Monitoring detects violations of the access policy." },
    ],
    mappings: [
      { framework_name: "NIST CSF 2.0", framework_name_en: "NIST Cybersecurity Framework 2.0", code: "PR.AA-01", title: "Identities and credentials are managed", strength: "full" },
      { framework_name: "ISO/IEC 27001:2022", framework_name_en: "ISO/IEC 27001:2022", code: "A.5.15", title: "Access control", strength: "partial" },
    ],
    implementations: [
      { id: 1, control_id: id, tech_asset_id: 1, description: "Role-based access in the identity provider", how_enforced: "Group-based policies synced nightly", status: "implemented", na_justification: null, owner_user_id: 2, last_verified_at: "2026-09-30T08:00:00Z" },
    ],
    evidence: [
      { id: 1, evidence_type_id: 1, control_id: id, tech_asset_id: 1, title: "Quarterly access review sign-off", owner_user_id: 2, location_hint: "GRC share / Q3", last_collected_at: "2026-09-28T08:00:00Z", valid_until: "2026-12-31", file_path: null, status: "valid", display_status: "valid", evidence_type_name: "Review record", tech_asset_name: "Identity provider" },
      { id: 2, evidence_type_id: 2, control_id: id, tech_asset_id: null, title: "Policy approval minutes", owner_user_id: 1, location_hint: "Board portal", last_collected_at: "2025-08-10T08:00:00Z", valid_until: "2026-08-10", file_path: null, status: "expired", display_status: "expired", evidence_type_name: "Approval", tech_asset_name: null },
    ],
  };
}

const TREE = [
  { id: 10, parent_id: null, code: "GV", title: "Govern", description: "Establish and monitor cybersecurity risk management strategy.", level: 1, order_index: 1, attributes: null },
  { id: 11, parent_id: 10, code: "GV.PO-01", title: "Policy for managing cybersecurity risks is established", description: "", level: 2, order_index: 1, attributes: null },
  { id: 12, parent_id: 10, code: "GV.RR-02", title: "Roles and responsibilities are established", description: "", level: 2, order_index: 2, attributes: null },
  { id: 20, parent_id: null, code: "PR", title: "Protect", description: "Safeguards to manage cybersecurity risks are used.", level: 1, order_index: 2, attributes: null },
  { id: 21, parent_id: 20, code: "PR.AA-01", title: "Identities and credentials are managed", description: "", level: 2, order_index: 1, attributes: null },
  { id: 22, parent_id: 20, code: "PR.AA-03", title: "Users and services are authenticated", description: "", level: 2, order_index: 2, attributes: null },
  { id: 23, parent_id: 20, code: "PR.DS-11", title: "Backups of data are created and tested", description: "", level: 2, order_index: 3, attributes: null },
  { id: 30, parent_id: null, code: "DE", title: "Detect", description: "Possible attacks and compromises are found and analyzed.", level: 1, order_index: 3, attributes: null },
  { id: 31, parent_id: 30, code: "DE.CM-01", title: "Networks are monitored", description: "", level: 2, order_index: 1, attributes: null },
];

const COVERAGE = [
  { item_id: 10, code: "GV", title: "Govern", level: 1, parent_id: null, requirements: 8, covered: 6 },
  { item_id: 20, code: "PR", title: "Protect", level: 1, parent_id: null, requirements: 14, covered: 9, superseded_only: 1 },
  { item_id: 30, code: "DE", title: "Detect", level: 1, parent_id: null, requirements: 6, covered: 5 },
];

const GAPS = [
  { item_id: 23, code: "PR.DS-11", title: "Backups of data are created and tested", has_supporting: true },
  { item_id: 12, code: "GV.RR-02", title: "Roles and responsibilities are established", has_supporting: false },
];

function maturitySummary() {
  const items = [
    [11, 10, "GV.PO-01", "Policy for managing cybersecurity risks is established", 4, 3],
    [12, 10, "GV.RR-02", "Roles and responsibilities are established", 2, 2],
    [21, 20, "PR.AA-01", "Identities and credentials are managed", 4, 4],
    [22, 20, "PR.AA-03", "Users and services are authenticated", 3, 3],
    [23, 20, "PR.DS-11", "Backups of data are created and tested", 3, 1],
    [31, 30, "DE.CM-01", "Networks are monitored", 4, 3],
  ] as const;
  const group = (id: number, code: string, title: string) => {
    const mine = items.filter((it) => it[1] === id);
    const avg = (k: 4 | 5) => Math.round((mine.reduce((s, it) => s + it[k], 0) / mine.length) * 10) / 10;
    return { framework_item_id: id, code, title, total_items: mine.length, scored_items: mine.length, doc_average: avg(4), impl_average: avg(5) };
  };
  return {
    assessment_id: 1, framework_id: 1,
    overall: { total_items: 6, scored_items: 6, doc_average: 3.3, impl_average: 2.7 },
    groups: [group(10, "GV", "Govern"), group(20, "PR", "Protect"), group(30, "DE", "Detect")],
    items: items.map(([id, parent, code, title, doc, impl]) => ({
      framework_item_id: id, parent_id: parent, group_id: parent, code, title, doc_score: doc, impl_score: impl,
      doc_rationale: "Documented in the approved policy set.", impl_rationale: impl < 2 ? "Restore tests not evidenced in the last 12 months." : "Operating evidence collected this quarter.",
    })),
  };
}

function graph() {
  const n = (key: string, kind: "control" | "framework_item", code: string, title: string, group: string, gap = false) => ({
    key, kind, code, title, group, group_extra: [], functions: [], pending_edges: 0, is_gap: gap,
  });
  const e = (source: string, target: string, kind: string, status: "confirmed" | "pending" = "confirmed") => ({
    key: `${source}-${target}`, source, target, kind, status, confidence: status === "pending" ? 0.82 : null, rationale: "", proposal_id: null, conflict_count: 0,
  });
  return {
    nodes: [
      n("c1", "control", "AC-1", "Access Control Policy", "doc-1"),
      n("c2", "control", "IA-2", "Identification and Authentication", "doc-2"),
      n("c3", "control", "SC-7", "Boundary Protection", "doc-1"),
      n("c4", "control", "SI-4", "System Monitoring", "doc-1"),
      n("c5", "control", "CP-9", "System Backup", "doc-3"),
      n("c6", "control", "CM-3", "Configuration Change Control", "doc-3"),
    ],
    edges: [
      e("c1", "c2", "depends_on"), e("c4", "c1", "monitors"), e("c3", "c4", "related"),
      e("c6", "c3", "supports"), e("c5", "c6", "related", "pending"),
    ],
    groups: [
      { key: "doc-1", kind: "document", label: "Information Security Policy" },
      { key: "doc-2", kind: "document", label: "Identity Management Standard" },
      { key: "doc-3", kind: "document", label: "IT Operations Standard" },
    ],
    stats: { nodes: 6, edges: 5, pending_edges: 1, truncated: false },
  };
}

export function getMockDetail(url: string, init: RequestInit): unknown {
  if ((init.method ?? "GET").toUpperCase() !== "GET") return undefined;
  let m = url.match(/^\/api\/controls\/(\d+)$/);
  if (m) return controlDetail(Number(m[1]));
  m = url.match(/^\/api\/frameworks\/\d+\/(tree|coverage|gaps|superseded-support)$/);
  if (m) {
    if (m[1] === "tree") return TREE;
    if (m[1] === "coverage") return COVERAGE;
    if (m[1] === "gaps") return GAPS;
    return [{ item_id: 21, code: "PR.AA-01", title: "Identities and credentials are managed", controls: ["IA-2"] }];
  }
  if (url === "/api/maturity/assessments") {
    return [{ id: 1, framework_id: 1, name: "FY26 Q3 self-assessment", as_of_date: "2026-09-30", status: "draft" }];
  }
  if (/^\/api\/maturity\/assessments\/\d+\/summary$/.test(url)) return maturitySummary();
  if (url === "/api/graph/relations" || url === "/api/graph/mappings") return graph();
  return undefined;
}
