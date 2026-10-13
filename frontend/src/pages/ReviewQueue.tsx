import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { request } from "../api";
import { useAuth } from "../auth";

export interface ControlSource {
  clause_id: number;
  citation_label: string | null;
  heading_path: string | null;
  document_id: number;
  document_title: string;
}

export interface ControlView {
  id: number;
  code: string;
  title: string;
  statement: string;
  sources?: ControlSource[];
}

/** 抽取失败的批次：本该产出控制点的位置，什么都没出来。 */
interface Failure {
  id: number;
  document_id: number | null;
  llm_call_id: number | null;
  reject_reason: string;
  clause_ids: number[];
}

export interface Proposal {
  id: number;
  kind: string;
  payload: Record<string, unknown>;
  citations: { clause_id: number; quote: string; document_id?: number; citation_label?: string; document_title?: string; heading_path?: string }[];
  confidence: number | null;
  document_id: number | null;
  status: string;
  decided_payload?: Record<string, unknown> | null;
  // 在接口把「够不够格批量接受」开放给所有 READ 角色之前，这个字段是可选的。
  // **不要在前端猜阈值**——判据只有后端有。
  bulk_acceptable?: boolean;
  ocr_quality_flag?: boolean;
  /** 正文用了 must/shall，而被引原文里一个情态词都没有——结论强于出处。 */
  normative_drift?: boolean;
  extraction_review?: { document_id: number; inventory_id: number; finding_count: number } | null;
  /** 已有控制点的正文与本提案逐字相同。是事实不是判断——并入还是另建由审核者选。 */
  duplicate_of?: { id: number; code: string; title: string } | null;
  mapping_context?: {
    framework_item: { id: number; code: string; title: string; description: string };
    control: ControlView;
    item_coverage?: {
      closed: boolean;
      confirmed: { control_code: string; strength: string }[];
    };
  } | null;
  relation_context?: {
    relation_type?: string;
    from: ControlView;
    to: ControlView;
  } | null;
  review_tier?: "auto" | "sample" | "manual" | "deferred";
  review_reasons?: string[];
}

interface AutomationItem {
  id: number;
  kind: string;
  confidence: number | null;
  source?: string | number;
  target?: string | number;
  strength?: unknown;
  relation_type?: unknown;
}

interface AutomationPreview {
  scanned: number;
  truncated: boolean;
  by_tier: Record<"auto" | "sample" | "manual" | "deferred", number>;
  by_kind: Record<string, Record<string, number>>;
  by_framework: Record<string, Record<string, number>>;
  by_reason: Record<string, number>;
  auto_items: AutomationItem[];
  sample_items: AutomationItem[];
  estimated_changes: { mappings: number; relations: number };
}

/** 一句话说明这张卡在主张什么、两栏各是什么。

    三种提案共用同一个左右布局，含义却完全不同：抽取卡右栏是「原文依据」，
    映射卡右栏是「你的控制点」。不写出来，读卡的人只能靠猜。 */
function CardClaim({ text }: { text: string }) {
  return (
    <p style={{ margin: "12px 0 0", fontSize: "0.8125rem", lineHeight: 1.6, color: "var(--text-tertiary)" }}>
      {text}
    </p>
  );
}

/** 控制点的出处。控制点是抽取产物，判断它满不满足某条要求之前，先得能回到原文核对。 */
function ControlSources({ sources }: { sources?: ControlSource[] }) {
  const { t } = useTranslation();
  if (!sources?.length) return null;
  return (
    <div style={{ marginTop: 12, fontSize: "0.8125rem", color: "var(--text-tertiary)" }}>
      <strong style={{ color: "var(--text-secondary)" }}>{t("review.source")}</strong>
      {sources.map((source) => (
        <div key={source.clause_id} style={{ marginTop: 4 }}>
          <span>{source.document_title}</span>
          {" · "}
          <Link to={`/documents/${source.document_id}#clause-${source.clause_id}`}>
            {source.citation_label ?? `#${source.clause_id}`}
          </Link>
          {source.heading_path && (
            <div style={{ fontSize: "0.75rem", opacity: 0.8 }}>{source.heading_path}</div>
          )}
        </div>
      ))}
    </div>
  );
}

/** 模型给出的理由。审核者判的就是这个主张成不成立，藏起来等于让人盲判。 */
function Rationale({ payload }: { payload: Record<string, unknown> }) {
  const { t } = useTranslation();
  const text = typeof payload.rationale === "string" ? payload.rationale.trim() : "";
  if (!text) return null;
  return (
    <div
      style={{
        marginTop: 16,
        padding: "10px 14px",
        fontSize: "0.875rem",
        lineHeight: 1.6,
        color: "var(--text-secondary)",
        background: "var(--stage-card-subtle)",
        borderLeft: "3px solid var(--accent-blue)",
        borderRadius: "var(--radius-xs)",
      }}
    >
      <strong style={{ color: "var(--text-primary)" }}>{t("review.rationale")}</strong>
      <div style={{ marginTop: 4 }}>{text}</div>
    </div>
  );
}

/** 编辑中的 payload 以 draft 为准；draft 尚未成形（正在手敲 JSON）时回落到原值。 */
function draftValue(draft: string, key: string, fallback: string): string {
  try {
    const parsed: unknown = JSON.parse(draft);
    const value = asRecord(parsed)[key];
    return typeof value === "string" ? value : fallback;
  } catch {
    return fallback;
  }
}

/** 改一个筛选、保留其余。

    此前每个筛选的 onChange 都是从零重建 query，切一个就把别的清空——
    只有两个筛选时不易察觉，加到四个就会立刻显形。 */
function withParam(current: URLSearchParams, key: string, value: string | string[]): URLSearchParams {
  const next = new URLSearchParams(current);
  next.delete(key);
  for (const v of Array.isArray(value) ? value : [value]) if (v) next.append(key, v);
  return next;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

type Citation = Proposal["citations"][number];

/** 同一条款的多条引文归到一个来源块下——条款是引用原子，落库时也按 clause_id 去重。 */
function groupByClause(citations: Citation[]): [number, Citation[]][] {
  const groups = new Map<number, Citation[]>();
  for (const citation of citations) {
    const bucket = groups.get(citation.clause_id);
    if (bucket) bucket.push(citation);
    else groups.set(citation.clause_id, [citation]);
  }
  return [...groups.entries()];
}

function HighlightQuote({ text, quote }: { text: string; quote: string }) {
  const position = quote ? text.indexOf(quote) : -1;
  if (position < 0) return <>{text}</>;
  return <>{text.slice(0, position)}<mark>{text.slice(position, position + quote.length)}</mark>{text.slice(position + quote.length)}</>;
}

function MappingPreview({
  proposal, editing, draft, busy, onDraftChange, onStrengthChange,
}: {
  proposal: Proposal;
  editing: boolean;
  draft: string;
  busy: boolean;
  onDraftChange: (value: string) => void;
  onStrengthChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const payload = proposal.payload;
  const item = proposal.mapping_context?.framework_item ?? asRecord(payload.framework_item);
  const control = proposal.mapping_context?.control ?? asRecord(payload.control);
  const itemCode = typeof item.code === "string" ? item.code : `#${String(payload.framework_item_id ?? "?")}`;
  const itemTitle = typeof item.title === "string" ? item.title : t("mapping.frameworkItem");
  const itemDescription = typeof item.description === "string" ? item.description : "";
  const controlCode = typeof control.code === "string" ? control.code : `#${String(payload.control_id ?? "?")}`;
  const controlTitle = typeof control.title === "string" ? control.title : t("mapping.control");
  const controlStatement = typeof control.statement === "string" ? control.statement : "";
  const quote = typeof payload.framework_item_quote === "string" ? payload.framework_item_quote : "";
  const proposed = typeof payload.strength === "string" ? payload.strength : "partial";
  // select 一旦绑死 payload.strength 就改不动：payload 是 AI 的原始产出、永不变，
  // 而 onStrengthChange 只写 draft。选完会被弹回原值，看起来就是「点了没反应」。
  const strength = editing ? draftValue(draft, "strength", proposed) : proposed;
  // 目标项已被已确认的 full/partial 关掉时，这条确认了也不改变覆盖度；若它本身
  // 是 supporting，连差距清单上的标记都不会新增——审之前就该看见这件事。
  const coverage = proposal.mapping_context?.item_coverage;
  const covered = coverage?.closed === true;
  const coveredBy = (coverage?.confirmed ?? [])
    .filter((row) => row.strength === "full" || row.strength === "partial")
    .map((row) => `${row.control_code} (${t(`mapping.strength.${row.strength}`, { defaultValue: row.strength })})`)
    .join(", ");

  return (
    <div style={{ marginTop: 16 }}>
      <CardClaim text={t("review.claimMapping", {
        strength: t(`mapping.strength.${strength}`, { defaultValue: strength }),
      })} />
      {covered && (
        <p
          role="note"
          style={{
            margin: "0 0 16px",
            padding: "10px 14px",
            fontSize: "0.875rem",
            lineHeight: 1.6,
            color: "var(--accent-amber)",
            background: "var(--accent-amber-bg)",
            border: "1px solid var(--accent-amber-border)",
            borderRadius: "var(--radius-xs)",
          }}
        >
          {t("mapping.alreadyCovered", { controls: coveredBy })}
          {strength === "supporting" && <> {t("mapping.supportingNoEffect")}</>}
        </p>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))", gap: 16, marginBottom: 16 }}>
        <div className="kn-card" style={{ background: "var(--stage-card-subtle)" }}>
          <h3 style={{ fontSize: "1.0625rem" }}>
            {t("mapping.frameworkItem")} · <code>{itemCode}</code> {itemTitle}
          </h3>
          <p style={{ whiteSpace: "pre-wrap", fontSize: "0.875rem", lineHeight: 1.6, color: "var(--text-secondary)" }}>
            <HighlightQuote text={itemDescription} quote={quote} />
          </p>
          <div style={{ marginTop: 12, padding: "8px 12px", background: "rgba(255, 214, 10, 0.08)", borderRadius: "var(--radius-xs)", border: "1px solid rgba(255, 214, 10, 0.2)" }}>
            <strong style={{ fontSize: "0.8125rem", color: "var(--accent-amber)" }}>{t("mapping.quote")}: </strong>
            <mark>{quote}</mark>
          </div>
        </div>
        <aside className="kn-card" style={{ background: "var(--stage-card-subtle)" }}>
          <h3 style={{ fontSize: "1.0625rem" }}>
            {t("mapping.control")} · <code>{controlCode}</code> {controlTitle}
          </h3>
          <p style={{ whiteSpace: "pre-wrap", fontSize: "0.875rem", lineHeight: 1.6, color: "var(--text-secondary)" }}>
            {controlStatement}
          </p>
          <ControlSources sources={proposal.mapping_context?.control.sources} />
        </aside>
      </div>

      <Rationale payload={payload} />

      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <label style={{ flexDirection: "row", alignItems: "center" }}>
          <span>{t("mapping.strength.label")}</span>
          <select
            aria-label={t("mapping.strength.label")}
            value={strength}
            disabled={busy}
            onChange={(event) => onStrengthChange(event.target.value)}
            style={{ padding: "6px 28px 6px 12px" }}
          >
            <option value="full">{t("mapping.strength.full")}</option>
            <option value="partial">{t("mapping.strength.partial")}</option>
            <option value="supporting">{t("mapping.strength.supporting")}</option>
          </select>
        </label>
      </div>

      {editing && (
        <label style={{ display: "block", marginTop: 16 }}>
          {t("review.payload")}
          <textarea
            aria-label={t("review.payload")}
            rows={8}
            style={{ width: "100%", boxSizing: "border-box" }}
            value={draft}
            disabled={busy}
            onChange={(event) => onDraftChange(event.target.value)}
          />
        </label>
      )}
    </div>
  );
}

function RelationBody({
  proposal, editing, draft, busy, onDraftChange,
}: {
  proposal: Proposal;
  editing: boolean;
  draft: string;
  busy: boolean;
  onDraftChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const context = proposal.relation_context;
  const payload = proposal.payload as Record<string, unknown>;
  const fromQuote = typeof payload.from_quote === "string" ? payload.from_quote : "";
  const toQuote = typeof payload.to_quote === "string" ? payload.to_quote : "";
  const relationType = context?.relation_type
    ?? (typeof payload.relation_type === "string" ? payload.relation_type : "");
  const label = relationType === "duplicates"
    ? t("relations.duplicates")
    : t("relations.dependsOn");
  const editor = editing && (
    <label style={{ display: "block", marginTop: 16 }}>
      {t("review.payload")}
      <textarea
        aria-label={t("review.payload")}
        rows={8}
        style={{ width: "100%", boxSizing: "border-box" }}
        value={draft}
        disabled={busy}
        onChange={(event) => onDraftChange(event.target.value)}
      />
    </label>
  );

  if (!context) {
    return (
      <div style={{ marginTop: 14 }}>
        <p><strong style={{ color: "var(--accent-blue)" }}>{label}</strong></p>
        <CardClaim text={t("review.claimRelation", { relation: label })} />
        <p role="note" style={{ color: "var(--accent-amber)" }}>{t("relations.contextMissing")}</p>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 13 }}>
          {JSON.stringify(proposal.payload, null, 2)}
        </pre>
        {editor}
      </div>
    );
  }

  return (
    <div style={{ marginTop: 14 }}>
      <p><strong style={{ color: "var(--accent-blue)" }}>{label}</strong></p>
      <CardClaim text={t("review.claimRelation", { relation: label })} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <section className="kn-card" style={{ background: "var(--stage-card-subtle)" }}>
          <h3 style={{ fontSize: "1rem" }}>
            {t("relations.from")} · <code>{context.from.code}</code> {context.from.title}
          </h3>
          <p style={{ whiteSpace: "pre-wrap", fontSize: "0.875rem", lineHeight: 1.6, color: "var(--text-secondary)" }}>
            <HighlightQuote text={context.from.statement} quote={fromQuote} />
          </p>
          <ControlSources sources={context.from.sources} />
        </section>
        <section className="kn-card" style={{ background: "var(--stage-card-subtle)" }}>
          <h3 style={{ fontSize: "1rem" }}>
            {t("relations.to")} · <code>{context.to.code}</code> {context.to.title}
          </h3>
          <p style={{ whiteSpace: "pre-wrap", fontSize: "0.875rem", lineHeight: 1.6, color: "var(--text-secondary)" }}>
            <HighlightQuote text={context.to.statement} quote={toQuote} />
          </p>
          <ControlSources sources={context.to.sources} />
        </section>
      </div>
      <Rationale payload={payload} />
      {editor}
    </div>
  );
}

interface ClauseDetail {
  id: number;
  document_id: number;
  document_title: string;
  citation_label: string;
  text: string;
}

function asClauseId(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : null;
}

function ConflictClausePane({
  clauseId, quote, sideLabel,
}: {
  clauseId: number | null;
  quote: string;
  sideLabel: string;
}) {
  const { t } = useTranslation();
  const { data, isPending, error } = useQuery({
    queryKey: ["clause", clauseId],
    queryFn: () => request<ClauseDetail>(`/api/clauses/${clauseId}`),
    enabled: clauseId != null,
  });

  return (
    <section className="kn-card" style={{ background: "var(--stage-card-subtle)" }}>
      <h3 style={{ fontSize: "1rem" }}>
        {sideLabel}
        {data && <> · {data.document_title}</>}
      </h3>
      {clauseId != null && isPending && (
        <p style={{ color: "var(--text-tertiary)", fontSize: "0.875rem" }}>{t("common.loading")}</p>
      )}
      {/* 审的就是这段原文。加载失败必须说出来，否则看着像一条空条款。 */}
      {error && (
        <p role="alert" style={{ fontSize: "0.875rem" }}>
          <span>⚠️</span> {error.message}
        </p>
      )}
      {data && (
        <>
          <Link to={`/documents/${data.document_id}#clause-${data.id}`}>
            <code>{data.citation_label}</code>
          </Link>
          <p style={{ whiteSpace: "pre-wrap", fontSize: "0.875rem", lineHeight: 1.6, color: "var(--text-secondary)" }}>
            <HighlightQuote text={data.text} quote={quote} />
          </p>
        </>
      )}
    </section>
  );
}

function ConflictBody({
  proposal, editing, draft, busy, onDraftChange,
}: {
  proposal: Proposal;
  editing: boolean;
  draft: string;
  busy: boolean;
  onDraftChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const payload = proposal.payload;
  const topic = typeof payload.topic === "string" ? payload.topic : "";
  const difference = typeof payload.difference === "string" ? payload.difference : "";
  const quoteA = typeof payload.quote_a === "string" ? payload.quote_a : "";
  const quoteB = typeof payload.quote_b === "string" ? payload.quote_b : "";

  return (
    <div style={{ marginTop: 14 }}>
      <p>
        <strong style={{ color: "var(--accent-blue)" }}>{t("review.conflictTopic")}</strong>
        {topic && <> · {topic}</>}
        {proposal.confidence != null && (
          <> · {t("review.confidence")} {proposal.confidence.toFixed(2)}</>
        )}
      </p>
      {/* 审的是两边条款原文是否真打架，不能只看模型抽出的片语。 */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <ConflictClausePane
          clauseId={asClauseId(payload.clause_a_id)}
          quote={quoteA}
          sideLabel={t("review.conflictSideA")}
        />
        <ConflictClausePane
          clauseId={asClauseId(payload.clause_b_id)}
          quote={quoteB}
          sideLabel={t("review.conflictSideB")}
        />
      </div>
      {difference && (
        <div
          style={{
            marginTop: 16,
            padding: "10px 14px",
            fontSize: "0.875rem",
            lineHeight: 1.6,
            color: "var(--text-secondary)",
            background: "var(--stage-card-subtle)",
            borderLeft: "3px solid var(--accent-blue)",
            borderRadius: "var(--radius-xs)",
          }}
        >
          <strong style={{ color: "var(--text-primary)" }}>{t("review.conflictDifference")}</strong>
          <div style={{ marginTop: 4 }}>{difference}</div>
        </div>
      )}
      {editing && (
        <label style={{ display: "block", marginTop: 16 }}>
          {t("review.payload")}
          <textarea
            aria-label={t("review.payload")}
            rows={8}
            style={{ width: "100%", boxSizing: "border-box" }}
            value={draft}
            disabled={busy}
            onChange={(event) => onDraftChange(event.target.value)}
          />
        </label>
      )}
    </div>
  );
}

export function ReviewQueue() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const canDecide = user?.role === "admin" || user?.role === "grc_lead";
  const canWrite = user?.role === "admin" || user?.role === "grc_lead";
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") ?? "";
  const documentId = params.get("document_id") ?? "";
  const strength = params.getAll("strength");
  const framework = params.get("framework") ?? "";
  const doubtful = params.get("doubtful_rationale") === "1";
  const showAll = params.get("show_all") !== "0";
  const [selected, setSelected] = useState<number[]>([]);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [rejecting, setRejecting] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showAutomationPreview, setShowAutomationPreview] = useState(false);

  const proposals = useQuery({
    queryKey: ["proposals", kind, documentId, strength.join(","), framework, doubtful, showAll],
    queryFn: () => {
      const query = new URLSearchParams({ limit: "200", ...(kind ? { kind } : {}), ...(documentId ? { document_id: documentId } : {}) });
      for (const value of strength) query.append("strength", value);
      if (framework) query.set("framework", framework);
      if (doubtful) query.set("doubtful_rationale", "true");
      if (!showAll) query.set("actionable_only", "true");
      return request<Proposal[]>(`/api/proposals?${query}`);
    },
    refetchInterval: 15000,
  });

  const frameworks = useQuery({
    queryKey: ["frameworks"],
    queryFn: () => request<{ key: string; name_zh: string; name_en: string }[]>("/api/frameworks"),
    enabled: params.get("kind") === "mapping",
  });
  const stats = useQuery({
    queryKey: ["proposal-stats"],
    queryFn: () => request<{ pending: number; by_kind: Record<string, number>; failed?: number }>("/api/proposals/stats"),
    refetchInterval: 15000,
  });
  const automationPreview = useQuery({
    queryKey: ["automation-preview"],
    queryFn: () => request<AutomationPreview>("/api/proposals/auto-process/preview"),
    enabled: showAutomationPreview,
  });

  // 抽取失败的批次。单独取而不是混进 proposals：它们没有东西可供人决策，
  // 混进去会让"还剩几条要看"这个数字失去意义。但必须看得见——不看见的话
  // 这一批就静默消失了，而审计员会以为文档就只抽出了这些控制点。
  const failures = useQuery({
    queryKey: ["proposal-failures"],
    // 只认带失败原因的行。这既是语义上的（没有原因就不是一条失败记录），
    // 也是防御性的：这个位置在队列最上面，一个意外的返回值不该让整页崩掉。
    queryFn: async () => {
      const rows = await request<Failure[]>("/api/proposals/failures");
      return Array.isArray(rows) ? rows.filter((row) => Boolean(row?.reject_reason)) : [];
    },
    refetchInterval: 30000,
  });

  // 重跑＝重新触发该文档的抽取。成功的批次有 checkpoint 会被跳过，
  // 失败的没有，所以这条天然只重试失败的那几批。
  const retry = useMutation({
    mutationFn: (documentId: number) =>
      request<{ job_id: string }>(`/api/extraction/documents/${documentId}`, { method: "POST" }),
    onSuccess: (result) => setNotice(t("review.retryQueued", { id: result.job_id })),
    onError: (e: Error) => setError(e.message),
  });

  async function refresh() {
    await Promise.all(["proposals", "proposal-stats", "proposal-failures", "automation-preview", "controls", "control"].map((key) => client.invalidateQueries({ queryKey: [key] })));
  }

  const decide = useMutation({
    mutationFn: ({ id, ...body }: { id: number; decision: "accept" | "modify" | "reject"; payload?: Record<string, unknown>; reason?: string; merge_into_control_id?: number }) =>
      request<Proposal>(`/api/proposals/${id}/decide`, { method: "POST", body: JSON.stringify(body) }),
    onMutate: () => { setError(""); setNotice(""); },
    onError: (e: Error) => setError(e.message),
    onSuccess: async (_, variables) => {
      setEditing(null); setRejecting(null); setReason("");
      setSelected((ids) => ids.filter((id) => id !== variables.id));
      setNotice(t("review.decided"));
      await refresh();
    },
  });

  const eligibleIds = (proposals.data ?? []).filter((p) => p.status === "pending" && p.bulk_acceptable === true && p.ocr_quality_flag !== true && p.id !== editing && p.id !== rejecting).map((p) => p.id);
  const selectedIds = selected.filter((id) => eligibleIds.includes(id));

  const bulk = useMutation({
    mutationFn: (ids: number[]) => request<{ accepted: number; skipped: number }>("/api/proposals/bulk-accept", { method: "POST", body: JSON.stringify({ ids }) }),
    onMutate: () => { setError(""); setNotice(""); },
    onError: (e: Error) => setError(e.message),
    onSuccess: async (result) => { setNotice(t("review.bulkResult", result)); setSelected([]); await refresh(); },
  });

  // 只执行预览清单上的那些：后端逐条重新判档，但不会碰清单之外的提案。
  // 曾经发的是 {limit: 200}，后端重新扫整个队列——预览之后新来的、以及超出
  // 清单的，都会被自动接受而没有人看过。
  const autoProcess = useMutation({
    mutationFn: (ids: number[]) => request<{ scanned: number; accepted: number; skipped: number; sample: number; manual: number; deferred: number }>(
      "/api/proposals/auto-process",
      { method: "POST", body: JSON.stringify({ ids }) },
    ),
    onMutate: () => { setError(""); setNotice(""); },
    onError: (e: Error) => setError(e.message),
    onSuccess: async (result) => { setNotice(t("review.autoResult", result)); await refresh(); },
  });

  const infer = useMutation({
    mutationFn: () => request<{ job_id: string }>("/api/relations/infer", { method: "POST" }),
    onMutate: () => { setError(""); setNotice(""); },
    onError: (e: Error) => setError(e.message),
    onSuccess: (result) => setNotice(t("relations.queued", { id: result.job_id })),
  });

  const embedVectors = useMutation({
    mutationFn: () => request<{ embedded: number; pending: number }>("/api/relations/embed", { method: "POST" }),
    onMutate: () => { setError(""); setNotice(""); },
    onError: (e: Error) => setError(e.message),
    onSuccess: (result) => setNotice(t("relations.embedded", { count: result.embedded, pending: result.pending })),
  });

  const busy = decide.isPending || bulk.isPending || autoProcess.isPending || infer.isPending || embedVectors.isPending;

  function modify(id: number) {
    try {
      const payload: unknown = JSON.parse(draft);
      if (!payload || typeof payload !== "object" || Array.isArray(payload) || !Object.keys(payload).length) throw new Error(t("review.invalidPayload"));
      const proposal = proposals.data?.find((p) => p.id === id);
      if (!proposal) return;
      const edited = payload as Record<string, unknown>;
      const next: Record<string, unknown> = proposal.kind === "matrix_mapping" ? { ...proposal.payload, mapping: edited } : { ...proposal.payload, ...edited };
      if (proposal.payload.origin === "matrix") {
        for (const field of ["origin", "matrix_mapping_proposal_id", "source_sha256", "row_number", "citations"]) next[field] = proposal.payload[field];
      }
      decide.mutate({ id, decision: "modify", payload: next });
    } catch { setError(t("review.invalidPayload")); }
  }

  return (
    <section>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 20 }}>
        <div>
          <h2>
            {t("review.title")}
            {stats.data && (
              <span className="kn-badge kn-badge-blue" style={{ marginLeft: 12, verticalAlign: "middle" }}>
                {t("review.pending")} {stats.data.pending}
              </span>
            )}
          </h2>
          <p style={{ margin: 0, fontSize: "0.875rem", color: "var(--text-secondary)" }}>
            {t("review.subtitle")}
          </p>
        </div>
      </div>

      {/* 抽取失败的批次。放在队列最上面：这些是"本该有东西却没有"的位置，
          比任何一条待确认提案都更需要先被看到。 */}
      {(failures.data?.length ?? 0) > 0 && (
        <div className="kn-card kn-failed-batches" style={{ marginBottom: 20 }}>
          <h3 style={{ margin: "0 0 4px", fontSize: "0.9375rem" }}>
            {t("review.failedBatches", { count: failures.data!.length })}
          </h3>
          <p style={{ margin: "0 0 12px", fontSize: "0.8125rem", color: "var(--text-secondary)" }}>
            {t("review.failedBatchesHint")}
          </p>
          <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: 8 }}>
            {failures.data!.map((failure) => (
              <li key={failure.id} className="kn-failed-batch-row">
                <div style={{ minWidth: 0 }}>
                  <code style={{ fontSize: "0.75rem" }}>{failure.reject_reason}</code>
                  <div style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", marginTop: 2 }}>
                    {t("review.failedBatchClauses", { count: failure.clause_ids?.length ?? 0 })}
                    {failure.document_id != null && ` · ${t("review.failedBatchDocument", { id: failure.document_id })}`}
                  </div>
                </div>
                {failure.document_id != null && (
                  <button
                    className="kn-btn-secondary kn-btn-sm"
                    disabled={retry.isPending}
                    onClick={() => retry.mutate(failure.document_id!)}
                  >
                    {t("review.retryBatch")}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Control Filter Bar */}
      <div
        className="kn-card"
        style={{
          padding: "14px 18px",
          display: "flex",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 20,
        }}
      >
        <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <span>{t("review.kind")}</span>
          <select
            aria-label={t("review.kind")}
            value={kind}
            disabled={busy}
            onChange={(e) => {
              setSelected([]); setEditing(null); setRejecting(null);
              // 换 kind 时清掉只对映射有意义的筛选：留着 strength 会让关系提案一条也筛不出来。
              let next = withParam(params, "kind", e.target.value);
              if (e.target.value !== "mapping") {
                for (const key of ["strength", "framework", "doubtful_rationale"]) next.delete(key);
                next = new URLSearchParams(next);
              }
              setParams(next);
            }}
            style={{ padding: "6px 28px 6px 12px" }}
          >
            <option value="">{t("review.allKinds")}</option>
            {[...new Set(["control_extract", "mapping", "matrix_mapping", ...Object.keys(stats.data?.by_kind ?? {})])].map((k) => (
              <option key={k} value={k}>{t(`review.kinds.${k}`, { defaultValue: k })}</option>
            ))}
          </select>
        </label>

        {kind === "mapping" && (
          <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <span>{t("review.framework")}</span>
            <select
              aria-label={t("review.framework")}
              value={framework}
              disabled={busy}
              onChange={(e) => {
                setSelected([]); setEditing(null); setRejecting(null);
                setParams(withParam(params, "framework", e.target.value));
              }}
              style={{ padding: "6px 28px 6px 12px" }}
            >
              <option value="">{t("review.allFrameworks")}</option>
              {(frameworks.data ?? []).map((f) => (
                <option key={f.key} value={f.key}>
                  {f.name_en || f.name_zh || f.key}
                </option>
              ))}
            </select>
          </label>
        )}

        <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <input
            type="checkbox"
            aria-label={t("review.showAll")}
            checked={showAll}
            disabled={busy}
            onChange={(e) => setParams(withParam(params, "show_all", e.target.checked ? "1" : "0"))}
          />
          <span>{t("review.showAll")}</span>
        </label>

        {kind === "mapping" && (
          <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <input
              type="checkbox"
              aria-label={t("review.doubtful")}
              checked={doubtful}
              disabled={busy}
              onChange={(e) => {
                setSelected([]); setEditing(null); setRejecting(null);
                setParams(withParam(params, "doubtful_rationale", e.target.checked ? "1" : ""));
              }}
            />
            <span title={t("review.doubtfulHint")}>{t("review.doubtful")}</span>
          </label>
        )}

        {kind === "mapping" && (
          <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <span>{t("review.strength")}</span>
            <select
              aria-label={t("review.strength")}
              value={strength.join(",")}
              disabled={busy}
              onChange={(e) => {
                setSelected([]); setEditing(null); setRejecting(null);
                setParams(withParam(params, "strength", e.target.value ? e.target.value.split(",") : []));
              }}
              style={{ padding: "6px 28px 6px 12px" }}
            >
              <option value="">{t("review.allStrengths")}</option>
              <option value="full,partial">{t("review.coverageAffecting")}</option>
              <option value="full">{t("mapping.strength.full")}</option>
              <option value="partial">{t("mapping.strength.partial")}</option>
              <option value="supporting">{t("mapping.strength.supporting")}</option>
            </select>
          </label>
        )}

        <button className="kn-btn-secondary kn-btn-sm" disabled={busy || proposals.isFetching} onClick={() => void refresh()}>
          {t("common.refresh")}
        </button>

        {documentId && (
          <button className="kn-btn-secondary kn-btn-sm" onClick={() => { setSelected([]); setParams(withParam(params, "document_id", "")); }}>
            {t("review.clearDocument")}
          </button>
        )}

        {canDecide && (
          <>
            <button
              className="kn-btn-primary kn-btn-sm"
              disabled={busy || proposals.isFetching || !selectedIds.length}
              onClick={() => bulk.mutate(selectedIds)}
            >
              {t("review.bulkAccept")} ({selectedIds.length})
            </button>
            <button
              className="kn-btn-secondary kn-btn-sm"
              disabled={busy || proposals.isFetching}
              onClick={() => setShowAutomationPreview(true)}
            >
              {t("review.previewAutomation")}
            </button>
          </>
        )}

        {canWrite && (
          <div style={{ display: "flex", gap: 8, marginLeft: "auto" }}>
            <button className="kn-btn-secondary kn-btn-sm" disabled={busy} onClick={() => infer.mutate()}>
              {t("relations.infer")}
            </button>
            <button className="kn-btn-secondary kn-btn-sm" disabled={busy} onClick={() => embedVectors.mutate()}>
              {t("relations.embed")}
            </button>
          </div>
        )}
      </div>

      {showAutomationPreview && (
        <section className="kn-card" aria-label={t("review.previewTitle")} style={{ marginBottom: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
            <div>
              <h3 style={{ margin: 0 }}>{t("review.previewTitle")}</h3>
              <p style={{ color: "var(--text-secondary)", fontSize: "0.8125rem", marginBottom: 0 }}>
                {t("review.previewReadOnly")}
              </p>
            </div>
            <button className="kn-btn-secondary kn-btn-sm" onClick={() => setShowAutomationPreview(false)}>
              {t("common.close", { defaultValue: "Close" })}
            </button>
          </div>
          {automationPreview.isPending && <p role="status">{t("common.loading")}</p>}
          {automationPreview.error && <p role="alert">⚠️ {automationPreview.error.message}</p>}
          {automationPreview.data && (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12, marginTop: 18 }}>
                {(["auto", "sample", "manual", "deferred"] as const).map((tier) => (
                  <div key={tier} className="kn-card" style={{ background: "var(--stage-card-subtle)", padding: 14 }}>
                    <div style={{ color: "var(--text-tertiary)", fontSize: "0.75rem" }}>{t(`review.tiers.${tier}`)}</div>
                    <strong style={{ fontSize: "1.5rem" }}>{automationPreview.data.by_tier[tier] ?? 0}</strong>
                  </div>
                ))}
              </div>
              <p style={{ color: "var(--text-secondary)", fontSize: "0.875rem" }}>
                {t("review.previewImpact", automationPreview.data.estimated_changes)}
              </p>
              {/* 扫描有上限：到了上限，上面的数字只是一部分。执行只处理清单里列出的那些。 */}
              {automationPreview.data.truncated && (
                <p role="note" style={{ color: "var(--accent-amber)", fontSize: "0.8125rem" }}>
                  {t("review.previewTruncated", { count: automationPreview.data.scanned })}
                </p>
              )}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))", gap: 16 }}>
                <div>
                  <h4>{t("review.byFramework")}</h4>
                  {Object.entries(automationPreview.data.by_framework).map(([framework, tiers]) => (
                    <div key={framework} style={{ fontSize: "0.8125rem", marginBottom: 6 }}>
                      <code>{framework}</code> · {t("review.autoShort")} {tiers.auto ?? 0} · {t("review.sampleShort")} {tiers.sample ?? 0}
                    </div>
                  ))}
                </div>
                <div>
                  <h4>{t("review.autoItems")}</h4>
                  <div style={{ maxHeight: 180, overflowY: "auto", fontSize: "0.8125rem" }}>
                    {automationPreview.data.auto_items.map((item) => (
                      <div key={item.id}>#{item.id} · {item.source ?? "?"} → {item.target ?? "?"} · {(item.confidence ?? 0).toFixed(2)}</div>
                    ))}
                    {!automationPreview.data.auto_items.length && <span>{t("review.none")}</span>}
                  </div>
                </div>
                <div>
                  <h4>{t("review.sampleItems")}</h4>
                  <div style={{ maxHeight: 180, overflowY: "auto", fontSize: "0.8125rem" }}>
                    {automationPreview.data.sample_items.map((item) => (
                      <div key={item.id}>#{item.id} · {item.source ?? "?"} → {item.target ?? "?"} · {(item.confidence ?? 0).toFixed(2)}</div>
                    ))}
                    {!automationPreview.data.sample_items.length && <span>{t("review.none")}</span>}
                  </div>
                </div>
              </div>
              {canDecide && (() => {
                // 数字按清单算，不按自动档总数：清单最多列 200 条，执行也只执行清单。
                const listed = automationPreview.data.auto_items.map((item) => item.id);
                return (
                  <button
                    className="kn-btn-primary"
                    style={{ marginTop: 18 }}
                    disabled={busy || listed.length === 0}
                    onClick={() => {
                      if (window.confirm(t("review.autoConfirmCount", { count: listed.length }))) autoProcess.mutate(listed);
                    }}
                  >
                    {t("review.executeAutomation", { count: listed.length })}
                  </button>
                );
              })()}
            </>
          )}
        </section>
      )}

      {!canDecide && <p style={{ color: "var(--text-tertiary)", fontSize: "0.875rem" }}>{t("review.readOnly")}</p>}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert"><span>⚠️</span> {error}</p>}
      {proposals.isPending && <p role="status">{t("common.loading")}</p>}
      {proposals.error && <p role="alert"><span>⚠️</span> {proposals.error.message}</p>}
      {stats.error && <p role="alert"><span>⚠️</span> {t("review.statsError")} {stats.error.message}</p>}
      {proposals.isSuccess && proposals.data.length === 0 && (
        <div className="kn-card" style={{ textAlign: "center", padding: "48px 20px" }}>
          <div style={{ fontSize: "2rem", marginBottom: 8, opacity: 0.5 }}>✓</div>
          <p style={{ color: "var(--text-secondary)", margin: 0 }}>{t("review.empty")}</p>
        </div>
      )}
      {proposals.data && proposals.data.length >= 200 && <p style={{ color: "var(--text-tertiary)", fontSize: "0.8125rem" }}>{t("review.limit")}</p>}

      {/* Proposal Cards Stream */}
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {proposals.data?.map((p) => {
          const confidence = p.confidence ?? 0;
          const confBadgeClass = confidence >= 0.85 ? "kn-badge-emerald" : confidence >= 0.65 ? "kn-badge-amber" : "kn-badge-ruby";

          return (
            <article
              key={p.id}
              id={`proposal-${p.id}`}
              className="kn-card"
              data-proposal-kind={p.kind}
              style={{ padding: "20px 24px" }}
            >
              {/* Proposal Header */}
              <div style={{ display: "flex", gap: 12, justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", borderBottom: "1px solid var(--stage-border)", paddingBottom: 14 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ fontSize: "1.0625rem", fontWeight: 700, color: "var(--text-primary)" }}>
                    #{p.id}
                  </span>
                  <span className="kn-badge">
                    {t(`review.kinds.${p.kind}`, { defaultValue: p.kind })}
                  </span>
                  <span className={`kn-badge ${confBadgeClass}`}>
                    <span className={`kn-dot ${confidence >= 0.85 ? "kn-dot-emerald" : confidence >= 0.65 ? "kn-dot-amber" : "kn-dot-ruby"}`} />
                    {t("review.confidence")} {p.confidence?.toFixed(2) ?? "—"}
                  </span>
                  {p.review_tier && (
                    <span className={`kn-badge ${p.review_tier === "auto" ? "kn-badge-emerald" : p.review_tier === "sample" || p.review_tier === "manual" ? "kn-badge-amber" : ""}`}>
                      {t(`review.tiers.${p.review_tier}`)}
                    </span>
                  )}
                </div>

                {canDecide && p.kind !== "conflict" && (
                  <label style={{ flexDirection: "row", alignItems: "center", gap: 6, margin: 0 }}>
                    <input
                      type="checkbox"
                      aria-label={t("review.select", { id: p.id })}
                      disabled={busy || !eligibleIds.includes(p.id)}
                      title={p.bulk_acceptable ? undefined : t("review.bulkUnavailable")}
                      checked={selectedIds.includes(p.id)}
                      onChange={(e) => setSelected(e.target.checked ? [...selected, p.id] : selected.filter((id) => id !== p.id))}
                    />
                    <span style={{ fontSize: "0.8125rem", color: "var(--text-secondary)" }}>{t("review.batch")}</span>
                  </label>
                )}
              </div>

              {p.extraction_review && <p role="note" style={{ color: "var(--accent-amber)" }}>
                {t("inventory.batchFindings", { count: p.extraction_review.finding_count })}{" "}
                <Link to={`/documents/${p.extraction_review.document_id}`}>{t("inventory.inspectFindings")}</Link>
              </p>}
              {p.normative_drift && (
                <p
                  role="note"
                  title={t("review.driftHint")}
                  style={{ marginTop: 10, color: "var(--accent-amber)", fontSize: "0.8125rem" }}
                >
                  ⚠️ {t("review.drift")}
                </p>
              )}

              {p.duplicate_of && (
                <p
                  role="note"
                  data-duplicate-of={p.duplicate_of.code}
                  title={t("review.duplicateHint")}
                  style={{ marginTop: 10, color: "var(--accent-amber)", fontSize: "0.8125rem" }}
                >
                  ⚠️ {t("review.duplicateOf", { code: p.duplicate_of.code, title: p.duplicate_of.title })}
                </p>
              )}

              {p.ocr_quality_flag && (
                <div style={{ marginTop: 10 }}>
                  <span className="kn-badge kn-badge-amber">
                    🔍 {t("review.ocrSuspect")}
                  </span>
                </div>
              )}

              {canDecide && !p.ocr_quality_flag && p.bulk_acceptable !== true && (
                <p style={{ color: "var(--text-tertiary)", fontSize: "0.8125rem", marginTop: 8 }}>
                  {t(p.bulk_acceptable === false ? "review.manualOnly" : "review.eligibilityUnknown")}
                </p>
              )}

              {p.review_tier && p.review_tier !== "auto" && (
                <p style={{ color: "var(--text-tertiary)", fontSize: "0.8125rem", marginTop: 8 }}>
                  {t(`review.tierHints.${p.review_tier}`)}
                </p>
              )}

              {/* Card Body */}
              {p.kind === "mapping" ? (
                <MappingPreview
                  proposal={p}
                  editing={editing === p.id}
                  draft={draft}
                  busy={busy}
                  onDraftChange={setDraft}
                  onStrengthChange={(strength) => {
                    setEditing(p.id);
                    setDraft(JSON.stringify({ ...p.payload, strength }, null, 2));
                    setError("");
                  }}
                />
              ) : p.kind === "relation" ? (
                <RelationBody
                  proposal={p}
                  editing={editing === p.id}
                  draft={draft}
                  busy={busy}
                  onDraftChange={setDraft}
                />
              ) : p.kind === "conflict" ? (
                <ConflictBody
                  proposal={p}
                  editing={editing === p.id}
                  draft={draft}
                  busy={busy}
                  onDraftChange={setDraft}
                />
              ) : (
                <>
                <CardClaim text={t(p.kind === "control_extract" ? "review.claimExtract" : "review.claimGeneric")} />
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: 16, marginTop: 16 }}>
                  {/* Left Payload Pane */}
                  <div className="kn-card" style={{ background: "var(--stage-card-subtle)", margin: 0 }}>
                    <h3 style={{ fontSize: "1.0625rem", marginTop: 0 }}>
                      {String(p.payload.title ?? t("review.payload"))}
                    </h3>
                    {editing === p.id ? (
                      <label>
                        {t("review.payload")}
                        <textarea
                          aria-label={t("review.payload")}
                          rows={14}
                          style={{ width: "100%", boxSizing: "border-box" }}
                          value={draft}
                          disabled={busy}
                          onChange={(e) => setDraft(e.target.value)}
                        />
                      </label>
                    ) : (
                      <>
                        {typeof p.payload.statement === "string" && (
                          <p style={{ whiteSpace: "pre-wrap", fontSize: "0.875rem", lineHeight: 1.6, color: "var(--text-secondary)" }}>
                            {p.payload.statement}
                          </p>
                        )}
                        {typeof p.payload.body === "string" && (
                          <p style={{ whiteSpace: "pre-wrap", fontSize: "0.875rem", lineHeight: 1.6, color: "var(--text-secondary)" }}>
                            {p.payload.body}
                          </p>
                        )}
                        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: "0.75rem", maxHeight: 180, overflowY: "auto" }}>
                          {JSON.stringify(p.payload, null, 2)}
                        </pre>
                      </>
                    )}
                  </div>

                  {/* Right Evidence Pane */}
                  <aside className="kn-card" style={{ background: "var(--stage-card-subtle)", margin: 0 }}>
                    <h3 style={{ fontSize: "1.0625rem", marginTop: 0 }}>{t("review.evidence")}</h3>
                    {p.citations.length === 0 && (
                      <p style={{ color: "var(--text-tertiary)", fontSize: "0.875rem" }}>
                        {t(p.kind === "matrix_mapping" ? "review.mappingEvidence" : "review.noEvidence")}
                      </p>
                    )}
                    {groupByClause(p.citations).map(([clauseId, group]) => (
                      <div key={clauseId} style={{ marginBottom: 14, paddingBottom: 10, borderBottom: "1px solid var(--stage-border-subtle)" }}>
                        {group[0].document_title && (
                          <div style={{ fontWeight: 600, fontSize: "0.875rem", color: "var(--text-primary)" }}>
                            {group[0].document_title}
                          </div>
                        )}
                        {group[0].heading_path && (
                          <div style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", margin: "2px 0 4px 0" }}>
                            {group[0].heading_path}
                          </div>
                        )}
                        {(group[0].document_id ?? p.document_id) != null ? (
                          <Link to={`/documents/${group[0].document_id ?? p.document_id}#clause-${clauseId}`}>
                            <code>{group[0].citation_label ?? `#${clauseId}`}</code>
                          </Link>
                        ) : (
                          <code>#{clauseId}</code>
                        )}
                        {group.map((c, i) => (
                          <blockquote
                            key={i}
                            style={{
                              margin: "8px 0 0 0",
                              whiteSpace: "pre-wrap",
                              fontSize: "0.8125rem",
                              lineHeight: 1.5,
                              color: "var(--text-secondary)",
                              borderLeft: "2px solid var(--accent-blue)",
                              paddingLeft: 10,
                            }}
                          >
                            {c.quote}
                          </blockquote>
                        ))}
                      </div>
                    ))}
                  </aside>
                </div>
                </>
              )}

              {/* Action Buttons Bar */}
              {canDecide && p.status === "pending" && (
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--stage-border)" }}>
                  {editing !== p.id && rejecting !== p.id && (
                    <>
                      {p.duplicate_of ? (
                        <>
                          <button
                            className="kn-btn-primary"
                            disabled={busy}
                            onClick={() => decide.mutate({ id: p.id, decision: "accept", merge_into_control_id: p.duplicate_of!.id })}
                          >
                            {t("review.mergeInto", { code: p.duplicate_of.code })}
                          </button>
                          <button
                            className="kn-btn-secondary"
                            disabled={busy}
                            onClick={() => decide.mutate({ id: p.id, decision: "accept" })}
                          >
                            {t("review.createSeparate")}
                          </button>
                        </>
                      ) : (
                        <button
                          className="kn-btn-primary"
                          disabled={busy}
                          onClick={() => decide.mutate({ id: p.id, decision: "accept" })}
                        >
                          {t("review.accept")}
                        </button>
                      )}
                      <button
                        className="kn-btn-secondary"
                        disabled={busy}
                        onClick={() => {
                          setEditing(p.id); setRejecting(null);
                          const editable = p.kind === "matrix_mapping" ? p.payload.mapping : p.payload.origin === "matrix" ? Object.fromEntries(Object.entries(p.payload).filter(([field]) => !["origin", "matrix_mapping_proposal_id", "source_sha256", "row_number", "citations"].includes(field))) : p.payload;
                          setDraft(JSON.stringify(editable, null, 2)); setError("");
                        }}
                      >
                        {t("review.modify")}
                      </button>
                      <button
                        className="kn-btn-danger"
                        disabled={busy}
                        onClick={() => { setRejecting(p.id); setEditing(null); setReason(""); setError(""); }}
                      >
                        {t("review.reject")}
                      </button>
                    </>
                  )}
                  {editing === p.id && (
                    <>
                      <button
                        className="kn-btn-primary"
                        disabled={busy || !draft.trim()}
                        onClick={() => modify(p.id)}
                      >
                        {t("review.modify")}
                      </button>
                      <button
                        className="kn-btn-secondary"
                        disabled={busy}
                        onClick={() => setEditing(null)}
                      >
                        {t("common.cancel")}
                      </button>
                    </>
                  )}
                  {rejecting === p.id && (
                    <>
                      <input
                        aria-label={t("review.rejectReason")}
                        placeholder={t("review.rejectReason")}
                        value={reason}
                        disabled={busy}
                        onChange={(e) => setReason(e.target.value)}
                        style={{ flex: 1, minWidth: 220 }}
                      />
                      <button
                        className="kn-btn-danger"
                        disabled={busy || !reason.trim()}
                        onClick={() => decide.mutate({ id: p.id, decision: "reject", reason: reason.trim() })}
                      >
                        {t("review.reject")}
                      </button>
                      <button
                        className="kn-btn-secondary"
                        disabled={busy}
                        onClick={() => setRejecting(null)}
                      >
                        {t("common.cancel")}
                      </button>
                    </>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
