import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { type ParseWarningCode, parseErrorText, parseWarningText } from "../parseWarnings";
import { Link, useSearchParams } from "react-router-dom";

import { demoNotCovered, isDemoSession, request } from "../api";
import { reviewState } from "../reviewDue";

interface Coverage {
  document_id: number;
  clauses: number;
  normative_clauses: number;
  controls: number;
  proposals: number;
  uncovered_normative: number;
  never_extracted: boolean;
}

interface Doc {
  id: number;
  title: string;
  doc_type: string;
  status: string;
  version: string | null;
  owner: string | null;
  effective_date: string | null;
  review_due_date: string | null;
  original_filename: string;
  parse_error: string | null;
  parse_error_code?: string | null;
  parse_error_params?: Record<string, unknown> | null;
  parse_warnings: string | null;
  parse_warning_codes: ParseWarningCode[] | null;
  ocr_quality_flag: boolean;
}

const IN_FLIGHT = new Set(["uploaded", "parsing"]);

/** 抽取盲区那一格。没有数据时留空，不猜。 */
function ExtractionCell({ row }: { row?: Coverage }) {
  const { t } = useTranslation();
  if (!row) return <td>—</td>;
  if (row.never_extracted) {
    return (
      <td>
        <span className="kn-badge kn-badge-danger" title={t("documents.neverExtractedHint")}>
          {t("documents.neverExtracted")}
        </span>
      </td>
    );
  }
  return (
    <td className="kn-text-xs">
      <div>{t("documents.controlCount", { count: row.controls })}</div>
      {row.uncovered_normative > 0 && (
        <div className="kn-warn"
             title={t("documents.uncoveredHint")}>
          {t("documents.uncovered", { count: row.uncovered_normative })}
        </div>
      )}
    </td>
  );
}

export function Documents() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [pasting, setPasting] = useState<number | null>(null);
  const [pasted, setPasted] = useState("");
  const reviewFilter = params.get("review") ?? "";

  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: () => request<Doc[]>("/api/documents"),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((document) => IN_FLIGHT.has(document.status))
        ? 3000
        : false,
  });
  // 语料盲区：文档传了没抽取、规范条款没产出控制点。两者都是静默的——
  // 不显示出来，界面上没有任何地方会提示。
  const coverage = useQuery({
    queryKey: ["document-coverage"],
    queryFn: () => request<Coverage[]>("/api/documents/coverage"),
  });

  async function uploadOne(file: File): Promise<void> {
    const body = new FormData();
    body.append("file", file);
    body.append("title", file.name.replace(/\.(pdf|docx)$/i, ""));
    body.append("doc_type", /guideline/i.test(file.name) ? "guideline" : "procedure");

    // 演示拦截在 request() 里，但 mock 不看请求方法：POST 会拿到文档列表、看起来
    // 像成功，所以上传要先自己挡（2026-09-20 测试报告的 BUG-21）。
    // 其余走 request()：它按 code 查词条，自己拼 fetch 显示的是后端那句中文兜底。
    if (isDemoSession()) throw demoNotCovered();
    try {
      await request<Doc>("/api/documents", { method: "POST", body });
    } catch (caught) {
      throw new Error(`${file.name}: ${(caught as Error).message}`);
    }
  }

  async function uploadAll(files: FileList): Promise<void> {
    setError("");
    const selected = Array.from(files);
    setBusy(selected.map((file) => file.name));
    const failures: string[] = [];
    for (const file of selected) {
      try {
        await uploadOne(file);
      } catch (caught) {
        failures.push((caught as Error).message);
      }
    }
    setBusy([]);
    if (failures.length) setError(failures.join("；"));
    void queryClient.invalidateQueries({ queryKey: ["documents"] });
  }

  const submitPlainText = useMutation({
    mutationFn: (id: number) =>
      request<Doc>(`/api/documents/${id}/plain-text`, {
        method: "POST",
        body: JSON.stringify({ text: pasted }),
      }),
    onSuccess: () => {
      setPasting(null);
      setPasted("");
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onMutate: () => setError(""),
    onError: (caught: Error) => setError(caught.message),
  });

  const reparse = useMutation({
    mutationFn: (id: number) =>
      request<Doc>(`/api/documents/${id}/reparse`, { method: "POST" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["documents"] }),
    // 这一页别处没有显示它的地方：拒绝重新解析（例如条款已被控制点引用）会无声无息。
    onMutate: () => setError(""),
    onError: (caught: Error) => setError(caught.message),
  });

  const allDocs = documents.data ?? [];
  const overdueCount = allDocs.filter((doc) => reviewState(doc) === "overdue").length;
  const soonCount = allDocs.filter((doc) => reviewState(doc) === "soon").length;
  const visible = allDocs.filter((document) => {
    if (reviewFilter === "overdue" || reviewFilter === "soon") return reviewState(document) === reviewFilter;
    return true;
  });

  return (
    <section>
      <div className="kn-page-header">
        <div>
          <h2>{t("documents.title")}</h2>
          <p className="kn-page-subtitle">
            {t("documents.subtitle")}
          </p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "0.875rem" }}>
            {t("documents.review")}
            <select
              aria-label={t("documents.review")}
              value={reviewFilter === "overdue" || reviewFilter === "soon" ? reviewFilter : ""}
              onChange={(event) => {
                const next = new URLSearchParams(params);
                if (event.target.value) next.set("review", event.target.value);
                else next.delete("review");
                setParams(next, { replace: true });
              }}
            >
              <option value="">{t("documents.filterAll")}</option>
              <option value="overdue">{t("documents.reviewOverdue", { count: overdueCount })}</option>
              <option value="soon">{t("documents.reviewSoon", { count: soonCount })}</option>
            </select>
          </label>
          <span className="kn-badge kn-badge-blue">
            {t("documents.total", { count: documents.data?.length ?? 0 })}
          </span>
        </div>
      </div>

      {/* Keynote Dropzone */}
      <div
        className="kn-dropzone"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          if (event.dataTransfer.files.length) void uploadAll(event.dataTransfer.files);
        }}
        onClick={() => fileInput.current?.click()}
      >
        <svg className="kn-dropzone-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="17 8 12 3 7 8" />
          <line x1="12" y1="3" x2="12" y2="15" />
        </svg>
        <div className="kn-dropzone-title">{t("documents.dropHint")}</div>
        <div className="kn-dropzone-subtitle">{t("documents.dropSubtitle")}</div>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept=".pdf,.docx"
          aria-label={t("documents.upload")}
          style={{ display: "none" }}
          onClick={(event) => event.stopPropagation()}
          onChange={(event) => event.target.files && void uploadAll(event.target.files)}
        />
      </div>

      {busy.length > 0 && (
        <p role="status">
          <span className="kn-dot kn-dot-blue kn-pulse" /> ⏳ {busy.join(", ")}
        </p>
      )}
      {error && (
        <p role="alert">
          <span>⚠️</span> {error}
        </p>
      )}

      {/* Documents Data Table */}
      <div className="kn-table-container">
        <table>
          <thead>
            <tr>
              <th>{t("documents.title")}</th>
              <th>{t("documents.docType")}</th>
              <th>{t("documents.status")}</th>
              <th>{t("documents.version")}</th>
              <th>{t("documents.owner")}</th>
              <th>{t("documents.effective")}</th>
              <th>{t("documents.review")}</th>
              <th title={t("documents.blindSpotHint")}>{t("documents.extraction")}</th>
              <th className="kn-text-right">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((document) => {
              const isFailed = document.status === "parse_failed";
              const isInFlight = IN_FLIGHT.has(document.status);
              const isActive = document.status === "active";
              const isOverdue = reviewState(document) === "overdue";

              return (
                <tr key={document.id}>
                  <td>
                    <div className="kn-row">
                      <Link
                        to={`/documents/${document.id}`} className="kn-strong kn-primary-text"
                      >
                        {document.title}
                      </Link>
                      {parseWarningText(document.parse_warnings, document.parse_warning_codes) && (
                        <span
                          title={
                            parseWarningText(
                              document.parse_warnings,
                              document.parse_warning_codes,
                            ) ?? undefined
                          }
                          className="kn-badge kn-badge-amber"
                          style={{ padding: "1px 6px", fontSize: "0.6875rem" }}
                        >
                          ⚠️
                        </span>
                      )}
                      {document.ocr_quality_flag && (
                        <span
                          title={t("documents.ocrSuspect")}
                          className="kn-badge kn-badge-purple"
                          style={{ padding: "1px 6px", fontSize: "0.6875rem" }}
                        >
                          🔍 OCR
                        </span>
                      )}
                    </div>
                  </td>
                  <td>
                    <span className="kn-badge" style={{ textTransform: "capitalize" }}>
                      {t(`documents.types.${document.doc_type}`, { defaultValue: document.doc_type })}
                    </span>
                  </td>
                  <td>
                    {isInFlight ? (
                      <span className="kn-badge kn-badge-blue kn-pulse">
                        <span className="kn-dot kn-dot-blue" />
                        ⏳ {t(`documents.statuses.${document.status}`, { defaultValue: document.status })}
                      </span>
                    ) : isActive ? (
                      <span className="kn-badge kn-badge-emerald">
                        <span className="kn-dot kn-dot-emerald" />
                        {t(`documents.statuses.${document.status}`, { defaultValue: document.status })}
                      </span>
                    ) : isFailed ? (
                      <div>
                        <span className="kn-badge kn-badge-ruby">
                          <span className="kn-dot kn-dot-ruby" />
                          {t(`documents.statuses.${document.status}`, { defaultValue: document.status })}
                        </span>
                        {parseErrorText(document) && (
                          <div style={{ color: "var(--accent-ruby)", fontSize: "0.75rem", marginTop: 4 }}>
                            {parseErrorText(document)}
                          </div>
                        )}
                      </div>
                    ) : (
                      <span className="kn-badge">{t(`documents.statuses.${document.status}`, { defaultValue: document.status })}</span>
                    )}
                  </td>
                  <td>{document.version ? <code>{document.version}</code> : "—"}</td>
                  <td>{document.owner ?? "—"}</td>
                  <td>{document.effective_date ?? "—"}</td>
                  <td style={{ color: isOverdue ? "var(--accent-ruby)" : undefined, fontWeight: isOverdue ? 600 : undefined }}>
                    {document.review_due_date ?? "—"}
                  </td>
                  <ExtractionCell row={(coverage.data ?? []).find((c) => c.document_id === document.id)} />
                  <td className="kn-text-right">
                    <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                      {document.status !== "superseded" && (
                        <button
                          className="kn-btn-sm kn-btn-secondary"
                          onClick={() => reparse.mutate(document.id)}
                        >
                          {t("documents.reparse")}
                        </button>
                      )}
                      {document.status === "parse_failed" && (
                        <button
                          className="kn-btn-sm kn-btn-primary"
                          onClick={() => setPasting(document.id)}
                        >
                          {t("documents.pasteText")}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Plain Text Fallback Drawer Card */}
      {pasting !== null && (
        <div className="kn-card" style={{ marginTop: 24, border: "1px solid var(--accent-blue)" }}>
          <h4 style={{ margin: "0 0 8px 0" }}>{t("documents.pasteText")}</h4>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.8125rem", marginBottom: 12 }}>
            {t("documents.pasteHint")}
          </p>
          <textarea
            aria-label={t("documents.pasteText")}
            rows={10}
            style={{ width: "100%", maxWidth: 840, marginBottom: 16 }}
            value={pasted}
            onChange={(event) => setPasted(event.target.value)}
          />
          <div style={{ display: "flex", gap: 10 }}>
            <button className="kn-btn-primary" onClick={() => submitPlainText.mutate(pasting)}>
              {t("common.save")}
            </button>
            <button className="kn-btn-secondary" onClick={() => setPasting(null)}>
              {t("common.cancel")}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
