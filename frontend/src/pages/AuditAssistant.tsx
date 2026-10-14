import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { download, request } from "../api";
import { useAuth } from "../auth";
import { LoadingState, PageHeader } from "../components/ui";

interface Engagement { id: number; name: string; audit_type: string; status: string; framework_id?: number | null }
interface Question { id: number; seq: number; question_text: string; language: string; status: string }
interface Answer {
  id: number; question_id: number; body: string; language: string; gap_notes: string;
  cited_clause_ids: number[]; cited_control_ids: number[]; suggested_evidence_ids: number[];
  confidence: number | null; final_body: string | null; finalized_at: string | null;
}
interface HistoryAnswer extends Answer { question_text: string; engagement_name: string }
interface SimilarAnswer {
  answer_id: number; question_id: number; question_text: string; engagement_name: string;
  answer: string; language: string; finalized_at: string; similarity: number;
}
interface PreflightRow {
  framework_item_id: number; code: string; title: string; likely_question: string;
  readiness: "green" | "yellow" | "red"; reason: string; control_count: number;
  implementation_count: number; evidence_count: number; valid_evidence_count: number;
  expired_evidence_count: number; evidence_titles: string[]; tool_names: string[];
}
interface Preflight { engagement_id: number; summary: Record<"green" | "yellow" | "red", number>; rows: PreflightRow[] }

export function AuditAssistant() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const client = useQueryClient();
  const canDraft = user?.role !== "viewer";
  const canFinalize = user?.role === "admin" || user?.role === "grc_lead";
  const [engagementId, setEngagementId] = useState<number | null>(null);
  const [questionId, setQuestionId] = useState<number | null>(null);
  const [engagementName, setEngagementName] = useState("");
  const [questionsText, setQuestionsText] = useState("");
  const [xlsxFile, setXlsxFile] = useState<File | null>(null);
  const [xlsxInputKey, setXlsxInputKey] = useState(0);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [preflightOpen, setPreflightOpen] = useState(false);
  const [readinessFilter, setReadinessFilter] = useState("all");

  const engagements = useQuery({
    queryKey: ["audit-engagements"],
    queryFn: () => request<Engagement[]>("/api/audit/engagements"),
  });
  useEffect(() => {
    if (!engagementId && engagements.data?.length) setEngagementId(engagements.data[0].id);
  }, [engagementId, engagements.data]);
  useEffect(() => { setPreflightOpen(false); }, [engagementId]);

  const questions = useQuery({
    queryKey: ["audit-questions", engagementId],
    queryFn: () => request<Question[]>(`/api/audit/engagements/${engagementId}/questions`),
    enabled: engagementId != null,
    refetchInterval: 10000,
  });
  useEffect(() => {
    if (questions.data?.length && !questions.data.some((row) => row.id === questionId)) {
      setQuestionId(questions.data[0].id);
    }
  }, [questionId, questions.data]);

  const answer = useQuery({
    queryKey: ["audit-answer", questionId],
    queryFn: () => request<Answer | null>(`/api/audit/questions/${questionId}/answer`),
    enabled: questionId != null,
    refetchInterval: 10000,
  });
  const similar = useQuery({
    queryKey: ["audit-similar-history", questionId],
    queryFn: () => request<SimilarAnswer[]>(`/api/audit/questions/${questionId}/similar-history`),
    enabled: questionId != null,
  });
  // 编辑框以哪一版为底（开始编辑时或最近一次同步到的正文）。答复每 10 秒刷新一次：
  // 原来一刷新就把编辑框整个换成库里的版本——别人一保存，你正在打的字就没了。
  // 现在：没改过就跟着新版本走；改过了就保留你的，另外提示有新版本。
  const [base, setBase] = useState<string | null>(null);
  const [incoming, setIncoming] = useState<string | null>(null);
  const synced = useRef<{ answerId: number | null; base: string | null; draft: string }>({ answerId: null, base: null, draft: "" });
  synced.current.draft = draft;
  useEffect(() => {
    const row = answer.data;
    if (!row) {
      synced.current = { answerId: null, base: null, draft: "" };
      setDraft(""); setBase(null); setIncoming(null);
      return;
    }
    const current = synced.current;
    if (row.id !== current.answerId) {
      synced.current = { answerId: row.id, base: row.body, draft: row.body };
      setDraft(row.body); setBase(row.body); setIncoming(null);
      return;
    }
    if (row.body === current.base) return;
    if (current.draft === current.base) {
      synced.current = { ...current, base: row.body, draft: row.body };
      setDraft(row.body); setBase(row.body); setIncoming(null);
    } else {
      setIncoming(row.body);
    }
  }, [answer.data]);
  function takeIncoming() {
    if (incoming === null) return;
    synced.current = { ...synced.current, base: incoming, draft: incoming };
    setDraft(incoming); setBase(incoming); setIncoming(null);
  }

  const history = useQuery({
    queryKey: ["audit-history"],
    queryFn: () => request<HistoryAnswer[]>("/api/audit/history"),
  });
  const preflight = useQuery({
    queryKey: ["audit-preflight", engagementId],
    queryFn: () => request<Preflight>(`/api/audit/engagements/${engagementId}/preflight?language=en`),
    enabled: preflightOpen && engagementId != null,
    retry: false,
  });
  const fail = (value: Error) => setError(value.message);
  const refresh = async () => {
    await Promise.all(["audit-engagements", "audit-questions", "audit-answer", "audit-history", "proposals", "proposal-stats"].map((key) => client.invalidateQueries({ queryKey: [key] })));
  };

  const createEngagement = useMutation({
    mutationFn: () => request<Engagement>("/api/audit/engagements", { method: "POST", body: JSON.stringify({ name: engagementName, audit_type: "external", status: "preparing" }) }),
    onError: fail,
    onSuccess: async (row) => { setError(""); setEngagementName(""); setEngagementId(row.id); setQuestionId(null); setNotice(t("audit.created")); await refresh(); },
  });
  const importQuestions = useMutation({
    mutationFn: () => request<Question[]>(`/api/audit/engagements/${engagementId}/questions`, { method: "POST", body: JSON.stringify({ text: questionsText, language: "en" }) }),
    onError: fail,
    onSuccess: async (rows) => { setError(""); setQuestionsText(""); setQuestionId(rows[0]?.id ?? null); setNotice(t("audit.imported", { count: rows.length })); await refresh(); },
  });
  const importXlsx = useMutation({
    mutationFn: () => {
      const form = new FormData();
      if (xlsxFile) form.append("file", xlsxFile);
      return request<Question[]>(`/api/audit/engagements/${engagementId}/questions/xlsx`, { method: "POST", body: form });
    },
    onError: fail,
    onSuccess: async (rows) => { setError(""); setXlsxFile(null); setXlsxInputKey((value) => value + 1); setQuestionId(rows[0]?.id ?? null); setNotice(t("audit.xlsxImported", { count: rows.length })); await refresh(); },
  });
  const exportWord = useMutation({
    mutationFn: () => download(`/api/audit/engagements/${engagementId}/export.docx?language=en`),
    onError: fail,
    onSuccess: (blob) => {
      setError("");
      const selectedEngagement = engagements.data?.find((row) => row.id === engagementId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${selectedEngagement?.name ?? "audit"}-audit-responses.docx`;
      anchor.click();
      URL.revokeObjectURL(url);
      setNotice(t("audit.exported"));
    },
  });
  const generateAll = useMutation({
    mutationFn: () => request<{ job_id: string; question_count: number }>(`/api/audit/engagements/${engagementId}/generate-all`, { method: "POST" }),
    onError: fail,
    onSuccess: async (row) => { setError(""); setNotice(t("audit.batchQueued", { count: row.question_count })); await refresh(); },
  });
  const generate = useMutation({
    mutationFn: (language: string) => request<{ proposal_id: number }>(`/api/audit/questions/${questionId}/generate`, { method: "POST", body: JSON.stringify({ language }) }),
    onError: fail,
    onSuccess: async (row) => { setError(""); setNotice(t("audit.generated", { id: row.proposal_id })); await refresh(); },
  });
  const save = useMutation({
    // 带上开始编辑时的那一版：库里已经被别人改了，后端拒绝而不是覆盖。
    mutationFn: () => request<Answer>(`/api/audit/answers/${answer.data?.id}`, { method: "PATCH", body: JSON.stringify({ body: draft, expected_body: base }) }),
    onError: async (value: Error) => { fail(value); await client.invalidateQueries({ queryKey: ["audit-answer", questionId] }); },
    onSuccess: async (row) => {
      synced.current = { ...synced.current, base: row.body, draft: row.body };
      setDraft(row.body); setBase(row.body); setIncoming(null);
      setError(""); setNotice(t("audit.saved")); await refresh();
    },
  });
  const finalize = useMutation({
    // 带上屏幕上的正文：库里的与它不同（别人刚保存过）后端会拒绝，而不是定下另一版。
    mutationFn: () => request<Answer>(`/api/audit/answers/${answer.data?.id}/finalize`, { method: "POST", body: JSON.stringify({ body: draft }) }),
    onError: async (value: Error) => { fail(value); await client.invalidateQueries({ queryKey: ["audit-answer", questionId] }); },
    onSuccess: async () => { setError(""); setNotice(t("audit.finalized")); await refresh(); },
  });
  const busy = createEngagement.isPending || importQuestions.isPending || importXlsx.isPending || exportWord.isPending || generateAll.isPending || generate.isPending || save.isPending || finalize.isPending;
  const selected = questions.data?.find((row) => row.id === questionId);
  // 定稿的是库里那一版；有没保存的修改时先存，否则定下来的、导出给审计师的不是屏幕上这一版。
  const unsaved = !!answer.data && draft.trim() !== answer.data.body.trim();

  return (
    <section>
      <PageHeader title={t("audit.title")} subtitle={t("audit.subtitle")} />
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">⚠️ {error}</p>}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(220px, .8fr) minmax(260px, 1fr) minmax(360px, 1.5fr)", gap: 16, alignItems: "start" }}>
        <aside className="kn-card">
          <h3>{t("audit.engagements")}</h3>
          {engagements.data?.map((row) => (
            <button key={row.id} className={row.id === engagementId ? "kn-btn-primary" : "kn-btn-secondary"} style={{ display: "block", width: "100%", marginBottom: 8, textAlign: "left" }} onClick={() => { setEngagementId(row.id); setQuestionId(null); }}>
              {row.name}<br /><small>{t(`audit.types.${row.audit_type}`)}</small>
            </button>
          ))}
          {canDraft && <div className="kn-divider-top">
            <input aria-label={t("audit.newEngagement")} placeholder={t("audit.newEngagement")} value={engagementName} onChange={(event) => setEngagementName(event.target.value)} />
            <button className="kn-mt-2 kn-btn-primary" disabled={busy || !engagementName.trim()} onClick={() => createEngagement.mutate()}>{t("common.create")}</button>
          </div>}
          {engagementId && <button className="kn-btn-secondary" disabled={busy} style={{ width: "100%", marginTop: 12 }} onClick={() => exportWord.mutate()}>{t("audit.exportWord")}</button>}
          {engagementId && canDraft && <button className="kn-w-full kn-mt-2 kn-btn-primary" disabled={busy || !questions.data?.some((row) => row.status === "pending")} onClick={() => generateAll.mutate()}>{t("audit.generateAll")}</button>}
          {engagementId && <button className="kn-w-full kn-mt-2 kn-btn-secondary" disabled={busy} onClick={() => setPreflightOpen((value) => !value)}>{t("audit.preflight")}</button>}
        </aside>

        <div className="kn-card">
          <h3>{t("audit.questions")}</h3>
          {questions.data?.map((row) => (
            <button key={row.id} className={row.id === questionId ? "kn-btn-primary" : "kn-btn-secondary"} style={{ display: "block", width: "100%", marginBottom: 8, textAlign: "left" }} onClick={() => setQuestionId(row.id)}>
              {row.seq}. {row.question_text}<br /><small>{t(`audit.status.${row.status}`)}</small>
            </button>
          ))}
          {engagementId && canDraft && <div className="kn-divider-top">
            <textarea aria-label={t("audit.pasteQuestions")} placeholder={t("audit.pasteQuestions")} rows={6} className="kn-w-full" value={questionsText} onChange={(event) => setQuestionsText(event.target.value)} />
            <button className="kn-btn-primary" disabled={busy || !questionsText.trim()} onClick={() => importQuestions.mutate()}>{t("audit.import")}</button>
            <div className="kn-divider-top">
              <label>
                {t("audit.xlsxFile")}
                <input key={xlsxInputKey} aria-label={t("audit.xlsxFile")} type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => setXlsxFile(event.target.files?.[0] ?? null)} />
              </label>
              <p className="kn-muted kn-text-2xs">{t("audit.xlsxHint")}</p>
              <button className="kn-btn-secondary" disabled={busy || !xlsxFile} onClick={() => importXlsx.mutate()}>{t("audit.importXlsx")}</button>
            </div>
          </div>}
        </div>

        <main className="kn-card">
          <h3>{selected?.question_text ?? t("audit.selectQuestion")}</h3>
          {selected && !answer.data && <>
            <p className="kn-secondary">{t("audit.noDraft")}</p>
            {canDraft && <div className="kn-row">
              <button className="kn-btn-primary" disabled={busy} onClick={() => generate.mutate("en")}>{t("audit.generateEn")}</button>
            </div>}
            {generate.isSuccess && <p><Link to="/review?kind=answer">{t("audit.reviewProposal")}</Link></p>}
          </>}
          {answer.data && <>
            <textarea aria-label={t("audit.answer")} rows={16} className="kn-w-full" value={draft} disabled={!!answer.data.finalized_at || !canDraft} onChange={(event) => setDraft(event.target.value)} />
            {incoming !== null && (
              <p role="status" className="kn-warn kn-text-xs">
                {t("audit.newerVersion")}{" "}
                <button type="button" className="kn-btn-secondary kn-btn-sm" onClick={takeIncoming}>{t("audit.loadNewerVersion")}</button>
              </p>
            )}
            {answer.data.gap_notes && <p className="kn-warn">⚠️ {answer.data.gap_notes}</p>}
            <p className="kn-muted kn-text-xs">{t("audit.references", { clauses: answer.data.cited_clause_ids.length, controls: answer.data.cited_control_ids.length, evidence: answer.data.suggested_evidence_ids.length })}</p>
            {!answer.data.finalized_at && canDraft && <button className="kn-btn-secondary" disabled={busy || !draft.trim()} onClick={() => save.mutate()}>{t("common.save")}</button>}
            {!answer.data.finalized_at && canFinalize && <button className="kn-ml-2 kn-btn-primary" disabled={busy || !draft.trim() || unsaved} onClick={() => finalize.mutate()}>{t("audit.finalize")}</button>}
            {!answer.data.finalized_at && canFinalize && unsaved && <p className="kn-warn kn-text-xs">{t("audit.unsavedBeforeFinalize")}</p>}
            {answer.data.finalized_at && <span className="kn-badge kn-badge-emerald">{t("audit.finalized")}</span>}
          </>}
          {selected && <div style={{ borderTop: "1px solid var(--stage-border)", marginTop: 20, paddingTop: 12 }}>
            <h4>{t("audit.similarHistory")}</h4>
            {similar.data?.map((row) => <details key={row.answer_id} className="kn-mb-2">
              <summary>{row.question_text} · {Math.round(row.similarity * 100)}%</summary>
              <p style={{ whiteSpace: "pre-wrap", color: "var(--text-secondary)" }}>{row.answer}</p>
              <small className="kn-muted">{row.engagement_name}</small>
            </details>)}
            {similar.data?.length === 0 && <p className="kn-muted">{t("audit.noSimilarHistory")}</p>}
          </div>}
        </main>
      </div>
      <div className="kn-mt-5 kn-card">
        <h3>{t("audit.history")}</h3>
        {history.data?.map((row) => <div key={row.id} className="kn-divider-row"><strong>{row.question_text}</strong><div className="kn-muted kn-text-2xs">{row.engagement_name}</div><p>{row.final_body}</p></div>)}
        {history.data?.length === 0 && <p className="kn-muted">{t("audit.noHistory")}</p>}
      </div>
      {preflightOpen && <div className="kn-mt-5 kn-card">
        <h3>{t("audit.preflightTitle")}</h3>
        {preflight.isLoading && <LoadingState />}
        {preflight.error && <p role="alert">⚠️ {(preflight.error as Error).message}</p>}
        {preflight.data && <>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
            {(["all", "green", "yellow", "red"] as const).map((value) => <button key={value} className={readinessFilter === value ? "kn-btn-primary" : "kn-btn-secondary"} onClick={() => setReadinessFilter(value)}>
              {t(`audit.readiness.${value}`)}{value === "all" ? ` (${preflight.data!.rows.length})` : ` (${preflight.data!.summary[value]})`}
            </button>)}
          </div>
          <div className="kn-table-container">
            <table>
              <thead><tr><th>{t("audit.readinessLabel")}</th><th>{t("audit.predictedQuestion")}</th><th>{t("audit.support")}</th></tr></thead>
              <tbody>{preflight.data.rows.filter((row) => readinessFilter === "all" || row.readiness === readinessFilter).map((row) => <tr key={row.framework_item_id}>
                <td><span className={`kn-badge kn-badge-${row.readiness === "green" ? "emerald" : row.readiness === "yellow" ? "amber" : "danger"}`}>{t(`audit.readiness.${row.readiness}`)}</span><div style={{ marginTop: 6, fontSize: "0.75rem", color: "var(--text-tertiary)" }}>{row.reason}</div></td>
                <td><strong>{row.code} {row.title}</strong><p>{row.likely_question}</p></td>
                <td>{t("audit.supportCounts", { controls: row.control_count, implementations: row.implementation_count, evidence: row.valid_evidence_count })}<div className="kn-muted kn-text-2xs">{row.tool_names.join(", ") || "—"}</div></td>
              </tr>)}</tbody>
            </table>
          </div>
        </>}
      </div>}
    </section>
  );
}
