import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ApiError, demoNotCovered, getToken, isDemoSession, request, setToken } from "../api";
import { type Issue, issueText } from "../i18n";
import { useAuth } from "../auth";
import type { Proposal } from "./ReviewQueue";
import { EmptyState, LoadingState } from "../components/ui";

export interface Control {
  id: number;
  code: string;
  title: string;
  statement: string;
  category: string | null;
  owner_user_id: number | null;
  status: string;
  merged_into_id: number | null;
}

const MAPPING_FIELDS = ["code", "title", "statement", "category", "owner", "framework_refs", "note"] as const;

interface SheetReport { ok: boolean; report: Issue[]; rows: number }

async function upload<T>(path: string, body: FormData): Promise<T> {
  // 上传/下载不走 request()，所以演示拦截也管不到这里——**必须自己挡一次**。
  // 不挡就会带着 demo-mock-token 打到真后端，401 之后 token 被清掉，人被踢回
  // 登录页（2026-09-20 测试报告的 BUG-21，以及那句"令牌无效或已过期"）。
  if (isDemoSession()) throw demoNotCovered();
  const token = getToken();
  const response = await fetch(path, { method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : {}, body });
  if (response.status === 401) setToken(null);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, data.code ?? "error", data.message ?? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? response.status)), data.params);
  return data as T;
}

function MatrixImport() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [report, setReport] = useState<SheetReport | null>(null);
  const [result, setResult] = useState<{ imported: number; proposal_ids: number[]; mapping_proposal_id: number } | null>(null);
  const [error, setError] = useState("");
  const [resumeId, setResumeId] = useState("");
  function body() {
    if (!file) throw new Error(t("matrix.chooseFile"));
    const form = new FormData(); form.append("file", file); return form;
  }
  async function refresh() {
    await Promise.all(["proposals", "proposal-stats", "controls"].map((key) => client.invalidateQueries({ queryKey: [key] })));
  }
  const propose = useMutation({
    mutationFn: () => upload<Proposal>("/api/matrix/propose-mapping", body()),
    onMutate: () => setError(""),
    onError: (e: Error) => setError(e.message),
    onSuccess: async (p) => { setProposal(p); setMapping(p.payload.mapping as Record<string, string>); setReport(null); setResult(null); await refresh(); },
  });
  const validate = useMutation({
    mutationFn: () => { const form = body(); form.append("mapping_json", JSON.stringify(mapping)); return upload<SheetReport>("/api/matrix/validate", form); },
    onMutate: () => { setError(""); setReport(null); },
    onError: (e: Error) => setError(e.message),
    onSuccess: setReport,
  });
  const approve = useMutation({
    mutationFn: () => {
      if (!proposal) throw new Error(t("matrix.chooseFile"));
      const original = proposal.payload.mapping as Record<string, string>;
      const changed = JSON.stringify(Object.entries(original).sort()) !== JSON.stringify(Object.entries(mapping).sort());
      return request<Proposal>(`/api/proposals/${proposal.id}/decide`, { method: "POST", body: JSON.stringify(changed ? { decision: "modify", payload: { ...proposal.payload, mapping } } : { decision: "accept" }) });
    },
    onMutate: () => setError(""),
    onError: (e: Error) => setError(e.message),
    onSuccess: async (p) => { setProposal(p); await refresh(); },
  });
  const importRows = useMutation({
    mutationFn: () => {
      if (!proposal && !resumeId) throw new Error(t("matrix.chooseFile"));
      const form = body(); form.append("proposal_id", proposal ? String(proposal.id) : resumeId);
      return upload<{ imported: number; proposal_ids: number[]; mapping_proposal_id: number }>("/api/matrix/import", form);
    },
    onMutate: () => setError(""),
    onError: (e: Error) => setError(e.message),
    onSuccess: async (data) => { setResult(data); await refresh(); },
  });
  const approved = proposal?.status === "accepted" || proposal?.status === "modified";
  const busy = propose.isPending || validate.isPending || approve.isPending || importRows.isPending;
  const headers = Array.isArray(proposal?.payload.source_headers) ? proposal.payload.source_headers.filter((h): h is string => typeof h === "string") : [];
  return <details className="kn-card kn-mb-5">
    <summary className="kn-text-md kn-primary-text">{t("matrix.title")}</summary>
    <p style={{ color: "var(--text-secondary)", fontSize: "0.875rem", margin: "8px 0 16px 0" }}>{t("matrix.flow")}</p>
    
    <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 16 }}>
      <label className="kn-inline-label">
        <span>{t("matrix.file")}</span>
        <input
          type="file"
          accept=".xlsx"
          disabled={busy}
          onChange={(e) => { setFile(e.target.files?.[0] ?? null); setProposal(null); setMapping({}); setReport(null); setResult(null); setError(""); }}
        />
      </label>
      <button
        className="kn-btn-primary kn-btn-sm"
        disabled={!file || busy || !!proposal}
        onClick={() => propose.mutate()}
      >
        {t("matrix.propose")}
      </button>
    </div>

    {!proposal && (
      <div className="kn-card" style={{ background: "var(--stage-card-subtle)", padding: 14, marginTop: 12 }}>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.8125rem", margin: "0 0 10px 0" }}>{t("matrix.resumeHint")}</p>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <label className="kn-inline-label">
            <span>{t("matrix.proposalId")}</span>
            <input
              type="number"
              min="1"
              step="1"
              value={resumeId}
              disabled={busy}
              onChange={(e) => { setResumeId(e.target.value); setResult(null); }}
              style={{ width: 120, padding: "5px 10px" }}
            />
          </label>
          <button
            className="kn-btn-primary kn-btn-sm"
            disabled={busy || !file || !Number.isSafeInteger(Number(resumeId)) || Number(resumeId) < 1 || !!result}
            onClick={() => importRows.mutate()}
          >
            {t("matrix.import")}
          </button>
        </div>
      </div>
    )}

    {busy && <LoadingState inline />}
    {error && <p role="alert"><span>⚠️</span> {error}</p>}

    {proposal && (
      <div className="kn-mt-4">
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
          <Link to="/review?kind=matrix_mapping" className="kn-badge kn-badge-blue">
            {t("review.title")} · #{proposal.id}
          </Link>
          <span className="kn-badge">{proposal.status}</span>
        </div>

        {typeof proposal.payload.notes === "string" && (
          <p className="kn-secondary kn-text-sm">{proposal.payload.notes}</p>
        )}
        <p style={{ fontWeight: 600, color: "var(--text-primary)", fontSize: "0.875rem" }}>
          {t("matrix.rows", { count: typeof proposal.payload.source_rows === "number" ? proposal.payload.source_rows : report?.rows ?? 0 })}
        </p>

        <div className="kn-table-container">
          <table>
            <thead>
              <tr>
                <th>{t("matrix.target")}</th>
                <th>{t("matrix.source")}</th>
              </tr>
            </thead>
            <tbody>
              {MAPPING_FIELDS.map((field) => (
                <tr key={field}>
                  <td className="kn-medium">
                    {t(`matrix.fields.${field}`)}{field === "title" || field === "statement" ? " *" : ""}
                  </td>
                  <td>
                    <select
                      aria-label={t(`matrix.fields.${field}`)}
                      value={mapping[field] ?? ""}
                      disabled={busy || approved}
                      onChange={(e) => {
                        const next = { ...mapping };
                        if (e.target.value) next[field] = e.target.value;
                        else delete next[field];
                        setMapping(next);
                        setReport(null);
                        setError("");
                      }}
                      style={{ padding: "6px 28px 6px 12px", width: "100%", maxWidth: 280 }}
                    >
                      <option value="">{t("matrix.unmapped")}</option>
                      {[...new Set([...headers, ...Object.values(mapping)])].map((header) => (
                        <option key={header} value={header}>{header}</option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 16 }}>
          <button
            className="kn-btn-secondary kn-btn-sm"
            disabled={busy || !mapping.title || !mapping.statement}
            onClick={() => validate.mutate()}
          >
            {t("matrix.validate")}
          </button>
          <button
            className="kn-btn-primary kn-btn-sm"
            disabled={busy || approved || !report?.ok}
            onClick={() => approve.mutate()}
          >
            {t("matrix.approve")}
          </button>
          <button
            className="kn-btn-primary kn-btn-sm"
            disabled={busy || !approved || !report?.ok || !!result}
            onClick={() => importRows.mutate()}
          >
            {t("matrix.import")}
          </button>
        </div>

        {report && (
          <div role={report.ok ? "status" : "alert"} className="kn-mt-3">
            <p>{t(report.ok ? "matrix.valid" : "matrix.invalid", { count: report.rows })}</p>
            <ul style={{ paddingLeft: 18, margin: "6px 0 0 0" }}>
              {report.report.map((issue, i) => <li key={i}>{issueText(issue)}</li>)}
            </ul>
          </div>
        )}
        {approved && !result && <p role="status">{t("matrix.approved")}</p>}
      </div>
    )}

    {result && (
      <p role="status">
        {t("matrix.result", { count: result.imported })} <Link to="/review?kind=control_extract">{t("review.title")}</Link>
      </p>
    )}
  </details>;
}

export function Controls() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const canWrite = user?.role === "admin" || user?.role === "grc_lead";
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [documentId, setDocumentId] = useState("");

  const controls = useQuery({
    queryKey: ["controls", search],
    queryFn: () => request<Control[]>(`/api/controls?limit=100&q=${encodeURIComponent(search)}`),
  });
  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: () => request<{ id: number; title: string; status: string }[]>("/api/documents"),
    enabled: canWrite,
  });
  const extract = useMutation({
    mutationFn: (id: string) => request<{ job_id: string }>(`/api/extraction/documents/${id}`, { method: "POST" }),
  });

  return (
    <section className="kn-page kn-page-controls">
      <div className="kn-page-header">
        <div>
          <h2>{t("controls.title")}</h2>
          <p className="kn-page-subtitle">
            {t("controls.subtitle")}
          </p>
        </div>
      </div>

      {canWrite && (
        <div className="kn-mb-5">
          <details className="kn-card kn-mb-4">
            <summary className="kn-text-md kn-primary-text">{t("controls.extract")}</summary>
            <p style={{ color: "var(--text-secondary)", fontSize: "0.875rem", margin: "8px 0 14px 0" }}>{t("controls.extractHint")}</p>
            {documents.isPending && <LoadingState />}
            {documents.error && (
              <p role="alert">
                <span>⚠️</span> {documents.error.message}{" "}
                <button className="kn-btn-sm" onClick={() => void documents.refetch()}>{t("common.retry")}</button>
              </p>
            )}
            <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
              <select
                aria-label={t("controls.document")}
                disabled={extract.isPending}
                value={documentId}
                onChange={(e) => { setDocumentId(e.target.value); extract.reset(); }}
                style={{ minWidth: 260 }}
              >
                <option value="">{t("controls.document")}</option>
                {documents.data?.filter((d) => d.status === "active").map((d) => (
                  <option key={d.id} value={d.id}>{d.title}</option>
                ))}
              </select>
              <button
                className="kn-btn-primary kn-btn-sm"
                disabled={!documentId || extract.isPending || extract.isSuccess}
                onClick={() => extract.mutate(documentId)}
              >
                {t("controls.extract")}
              </button>
            </div>
            {extract.isPending && <LoadingState inline />}
            {extract.error && <p role="alert"><span>⚠️</span> {extract.error.message}</p>}
            {extract.data && (
              <p role="status">
                {t("controls.queued", { id: extract.data.job_id })}{" "}
                <Link to={`/review?document_id=${documentId}`}>{t("review.title")}</Link>
              </p>
            )}
          </details>

          <MatrixImport />
        </div>
      )}

      {/* Search Header */}
      <form onSubmit={(e) => { e.preventDefault(); setSearch(q.trim()); }} className="kn-toolbar">
        <input
          aria-label={t("controls.search")}
          placeholder={t("controls.search")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ width: 340, maxWidth: "100%" }}
        />
        <button type="submit" className="kn-btn-primary kn-btn-sm">{t("search.run")}</button>
        <button type="button" className="kn-btn-secondary kn-btn-sm" disabled={controls.isFetching} onClick={() => void controls.refetch()}>
          {t("common.refresh")}
        </button>
      </form>

      {controls.isPending && <LoadingState />}
      {controls.error && <p role="alert"><span>⚠️</span> {controls.error.message}</p>}
      {controls.isSuccess && !controls.data.length && (
        <div className="kn-card kn-empty-card">
          <EmptyState compact />
        </div>
      )}
      {controls.data && controls.data.length >= 100 && (
        <p className="kn-muted kn-text-xs">{t("controls.limit")}</p>
      )}

      {!!controls.data?.length && (
        <div className="kn-table-container">
          <table>
            <thead>
              <tr>
                <th>{t("controls.code")}</th>
                <th>{t("controls.name")}</th>
                <th>{t("controls.statement")}</th>
                <th>{t("controls.category")}</th>
              </tr>
            </thead>
            <tbody>
              {controls.data.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link to={`/controls/${c.id}`}>
                      <code>{c.code}</code>
                    </Link>
                  </td>
                  <td>
                    <Link to={`/controls/${c.id}`} className="kn-strong kn-primary-text">
                      {c.title}
                    </Link>
                  </td>
                  <td style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", maxWidth: 560, lineHeight: 1.5 }}>
                    {c.statement.slice(0, 150)}{c.statement.length > 150 ? "…" : ""}
                  </td>
                  <td>
                    <span className="kn-badge">
                      {c.category ?? "—"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
