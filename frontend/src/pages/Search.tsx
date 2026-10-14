import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { request } from "../api";
import { useAuth } from "../auth";

interface Hit {
  chunk_id: number;
  clause_id: number;
  document_id: number;
  document_title: string;
  citation_label: string;
  heading_path: string;
  text: string;
  score: number;
  rank_fulltext: number | null;
  rank_vector: number | null;
}

interface SearchResponse {
  query: string;
  expanded_terms: string[];
  hits: Hit[];
  vector_used: boolean;
  vector_unavailable_reason?: "provider_unconfigured" | "index_incomplete" | "provider_error" | null;
}

interface IndexStatus {
  model: string;
  total: number;
  embedded: number;
  pending: number;
}

export function Search() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const client = useQueryClient();
  const canWrite = user?.role === "admin" || user?.role === "grc_lead" || user?.role === "contributor";
  const [draft, setDraft] = useState("");
  const [submitted, setSubmitted] = useState("");

  const status = useQuery({
    queryKey: ["index-status"],
    queryFn: () => request<IndexStatus>("/api/index/status"),
    refetchInterval: 5000,
  });

  const rebuild = useMutation({
    mutationFn: () => request<{ job_id: string }>("/api/index/rebuild", { method: "POST" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["index-status"] }),
  });

  const results = useQuery({
    queryKey: ["search", submitted],
    queryFn: () =>
      request<SearchResponse>(`/api/search?q=${encodeURIComponent(submitted)}&limit=20`),
    enabled: submitted.trim().length > 0,
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitted(draft);
  }

  return (
    <section>
      <div className="kn-mb-6">
        <h2>{t("search.title")}</h2>
        <p className="kn-page-subtitle">
          {t("search.subtitle")}
        </p>
      </div>

      {canWrite && (
        <p>
          <button className="kn-btn-secondary kn-btn-sm" disabled={rebuild.isPending} onClick={() => rebuild.mutate()}>
            {t("documents.rebuildIndex")}
          </button>
        </p>
      )}
      {rebuild.error && <p role="alert">{rebuild.error.message}</p>}
      {rebuild.isSuccess && <p role="status">{t("documents.indexQueued")}</p>}

      {status.data && status.data.total > 0 && (
        <div className="kn-card" style={{ padding: "12px 18px", marginBottom: 20, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div className="kn-row">
            <span className="kn-dot kn-dot-blue kn-pulse" />
            <span className="kn-secondary kn-text-xs">
              {t("search.indexing")}: <strong className="kn-primary-text">{status.data.embedded}</strong> / {status.data.total}
              {status.data.model ? ` · ${status.data.model}` : ""}
              {status.data.pending > 0 ? ` · ${t("search.indexIncomplete")}` : ""}
            </span>
          </div>
          <div style={{ width: 140 }}>
            <div className="kn-progress-bar">
              <div
                className="kn-progress-fill"
                style={{ width: `${Math.round((status.data.embedded / status.data.total) * 100)}%` }}
              />
            </div>
          </div>
        </div>
      )}

      {/* Keynote Spotlight Search Input Bar */}
      <form
        onSubmit={submit}
        style={{
          display: "flex",
          gap: 12,
          maxWidth: 780,
          marginBottom: 20,
          background: "var(--input-bg)",
          padding: 6,
          borderRadius: "var(--radius-pill)",
          border: "1px solid var(--stage-border)",
          boxShadow: "var(--shadow-glass)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", paddingLeft: 14, color: "var(--text-tertiary)" }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
        </div>
        <input
          maxLength={500}
          aria-label={t("search.title")}
          placeholder={t("search.placeholder")}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          style={{
            flex: 1,
            background: "transparent",
            border: "none",
            boxShadow: "none",
            fontSize: "1rem",
            padding: "8px 4px",
            color: "var(--text-primary)",
          }}
        />
        <button
          type="submit"
          className="kn-btn-wide kn-btn-primary"
        >
          {t("search.run")}
        </button>
      </form>

      {/* 检索失败原来什么都不显示——既没有结果，也没有「无结果」，看起来像没点到 */}
      {results.error && <p role="alert">⚠️ {results.error.message}</p>}
      {results.data && !results.data.vector_used && (
        <p role="status" style={{ maxWidth: 780, marginBottom: 16 }}>
          <span>⚠️</span> {t(results.data.vector_unavailable_reason ? `search.vectorReasons.${results.data.vector_unavailable_reason}` : "search.keywordOnly")}
        </p>
      )}

      {results.data && results.data.expanded_terms.length > 1 && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 20 }}>
          <span style={{ fontSize: "0.8125rem", color: "var(--text-tertiary)" }}>
            {t("search.expanded")}:
          </span>
          {results.data.expanded_terms.slice(1).map((term) => (
            <span key={term} className="kn-badge kn-badge-blue">
              {term}
            </span>
          ))}
        </div>
      )}

      {results.data && results.data.hits.length === 0 && submitted.trim() && (
        <div className="kn-card" style={{ textAlign: "center", padding: "48px 20px", maxWidth: 780 }}>
          <div className="kn-empty-emoji">🔍</div>
          <p className="kn-secondary kn-m-0">{t("search.noResults")}</p>
        </div>
      )}

      <ol style={{ paddingLeft: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 14, maxWidth: 840 }}>
        {results.data?.hits.map((hit) => (
          <li key={hit.chunk_id} className="kn-card kn-card-interactive" style={{ padding: "18px 22px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
              <div className="kn-row">
                <Link to={`/documents/${hit.document_id}#clause-${hit.clause_id}`} style={{ fontWeight: 600, fontSize: "1.0625rem", color: "var(--text-primary)" }}>
                  <strong>{hit.document_title}</strong>
                </Link>
                <Link to={`/documents/${hit.document_id}#clause-${hit.clause_id}`}>
                  <code>{hit.citation_label}</code>
                </Link>
              </div>
              <span className="kn-badge kn-badge-cyan" style={{ fontSize: "0.6875rem" }}>
                Score: {hit.score.toFixed(3)}
              </span>
            </div>
            <div style={{ color: "var(--text-tertiary)", fontSize: "0.75rem", marginBottom: 8 }}>
              {hit.heading_path}
            </div>
            <p style={{ margin: "8px 0 12px 0", whiteSpace: "pre-wrap", fontSize: "0.875rem", lineHeight: 1.6, color: "var(--text-secondary)" }}>
              {hit.text}
            </p>
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.75rem", color: "var(--text-secondary)" }}>
              <span>{t("search.matchedBy")}:</span>
              {[
                hit.rank_fulltext !== null
                  ? `${t("search.byKeyword")} #${hit.rank_fulltext}`
                  : null,
                hit.rank_vector !== null ? `${t("search.bySemantic")} #${hit.rank_vector}` : null,
              ]
                .filter(Boolean)
                .map((m) => (
                  <span key={m} className="kn-badge" style={{ fontSize: "0.6875rem" }}>
                    {m}
                  </span>
                ))}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
