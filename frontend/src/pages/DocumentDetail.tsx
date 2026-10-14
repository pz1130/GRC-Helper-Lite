import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { type ParseWarningCode, parseErrorText, parseWarningText } from "../parseWarnings";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { demoNotCovered, isDemoSession, request } from "../api";
import { useAuth } from "../auth";
import { RequirementInventory } from "../RequirementInventory";
import { DecisionScan } from "../DecisionScan";
import { OriginalSources } from "../OriginalSources";
import { EmptyState, LoadingState } from "../components/ui";

interface ClauseNode {
  id: number;
  number: string | null;
  heading: string;
  heading_path: string;
  citation_label: string;
  text: string;
  level: number;
  page_ref: number | null;
  kind: string;
  children: ClauseNode[];
}

interface Doc {
  id: number;
  title: string;
  doc_type: string;
  status: string;
  version: string | null;
  owner: string | null;
  parse_error: string | null;
  parse_error_code?: string | null;
  parse_error_params?: Record<string, unknown> | null;
  parse_warnings: string | null;
  parse_warning_codes: ParseWarningCode[] | null;
  supersedes_id: number | null;
}

interface ExtractionCoverage {
  counts: Record<"cited" | "uncited" | "not_completed", number>;
  clauses: { clause_id: number; citation_label: string; status: "cited" | "uncited" | "not_completed"; inventory_id?: number | null; inventory_status?: string | null }[];
  reference_issues?: { source_clause_id: number; label: string; status: "missing" | "ambiguous" | "context_limit" }[];
}

function flatten(nodes: ClauseNode[]): ClauseNode[] {
  return nodes.flatMap((node) => [node, ...flatten(node.children)]);
}

function Tree({
  nodes,
  onSelect,
  selected,
}: {
  nodes: ClauseNode[];
  onSelect: (node: ClauseNode) => void;
  selected: number | null;
}) {
  return (
    <ul style={{ listStyle: "none", paddingLeft: 12, margin: 0 }}>
      {nodes.map((node) => {
        const isSelected = selected === node.id;
        return (
          <li key={node.id} style={{ margin: "2px 0" }}>
            <button
              onClick={() => onSelect(node)}
              title={node.heading_path}
              style={{
                width: "100%",
                background: isSelected ? "rgba(41, 151, 255, 0.16)" : "transparent",
                // 选中项跟着主题取字色：写死白色时，浅色主题里是浅蓝底白字，看不见。
                color: isSelected ? "var(--text-primary)" : "var(--text-secondary)",
                border: isSelected ? "1px solid rgba(41, 151, 255, 0.35)" : "1px solid transparent",
                borderRadius: "var(--radius-sm)",
                padding: "6px 10px",
                cursor: "pointer",
                textAlign: "left",
                font: "inherit",
                fontSize: "0.8125rem",
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontWeight: isSelected ? 600 : 400,
                boxShadow: isSelected ? "0 2px 8px rgba(41, 151, 255, 0.2)" : "none",
              }}
            >
              {node.kind === "table" ? <span style={{ opacity: 0.7 }}>▦</span> : null}
              {node.number ? (
                <code style={{ fontSize: "0.75rem", padding: "1px 5px", background: "var(--code-bg)" }}>
                  {node.number}
                </code>
              ) : null}
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {node.heading}
              </span>
            </button>
            {node.children.length > 0 && (
              <Tree nodes={node.children} onSelect={onSelect} selected={selected} />
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function DocumentDetail() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { id } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const [selected, setSelected] = useState<ClauseNode | null>(null);
  const [importing, setImporting] = useState(false);
  const [mergeInto, setMergeInto] = useState("");
  const canEdit = user?.role !== "viewer";

  const doc = useQuery({
    queryKey: ["document", id],
    queryFn: () => request<Doc>(`/api/documents/${id}`),
    refetchInterval: (query) => ["uploaded", "parsing"].includes(query.state.data?.status ?? "") ? 2000 : false,
  });
  const clauses = useQuery({
    queryKey: ["clauses", id],
    queryFn: () => request<ClauseNode[]>(`/api/documents/${id}/clauses`),
    refetchInterval: ["uploaded", "parsing"].includes(doc.data?.status ?? "") ? 2000 : false,
  });
  const flat = clauses.data ? flatten(clauses.data) : [];
  const coverage = useQuery({
    queryKey: ["extraction-coverage", id, clauses.data],
    queryFn: () => request<ExtractionCoverage>(`/api/extraction/documents/${id}/coverage`),
    enabled: doc.data?.status === "active" && !isDemoSession(),
    refetchInterval: 10000,
  });

  useEffect(() => {
    if (doc.data?.status === "active") {
      void queryClient.invalidateQueries({ queryKey: ["clauses", id] });
    }
  }, [doc.data?.status, id, queryClient]);

  const rebuildIndex = useMutation({
    mutationFn: () => request<{ job_id: string }>(`/api/index/documents/${id}`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["index-status"] }),
  });
  const reparse = useMutation({
    mutationFn: () => request<Doc>(`/api/documents/${id}/reparse`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["document", id] }),
  });

  const uploadVersion = useMutation({
    mutationFn: async (file: File) => {
      const current = doc.data;
      if (!current) throw new Error("document not loaded");
      const body = new FormData();
      body.append("file", file);
      body.append("title", file.name.replace(/\.(pdf|docx)$/i, ""));
      body.append("doc_type", current.doc_type);
      body.append("supersedes_id", String(id));
      // 演示拦截在 request() 里，但 mock 不看请求方法：POST 会拿到文档列表、看起来
      // 像成功，所以上传要先自己挡（2026-09-20 测试报告的 BUG-21）。
      // 其余走 request()：它按 code 查词条，自己拼 fetch 显示的是后端那句中文兜底。
      if (isDemoSession()) throw demoNotCovered();
      return request<Doc>("/api/documents", { method: "POST", body });
    },
    onSuccess: (created) => {
      setImporting(false);
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      navigate(`/documents/${created.id}`);
    },
  });

  const split = useMutation({
    mutationFn: (at: number) =>
      request<ClauseNode>(`/api/clauses/${selected?.id}/split`, {
        method: "POST",
        body: JSON.stringify({ at }),
      }),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ["clauses", id] });
      await queryClient.invalidateQueries({ queryKey: ["source-blocks", id] });
      setSelected(created);
    },
  });
  const merge = useMutation({
    mutationFn: (intoId: number) =>
      request<ClauseNode>(`/api/clauses/${selected?.id}/merge`, {
        method: "POST",
        body: JSON.stringify({ into_id: intoId }),
      }),
    onSuccess: async (winner) => {
      await queryClient.invalidateQueries({ queryKey: ["clauses", id] });
      await queryClient.invalidateQueries({ queryKey: ["source-blocks", id] });
      setMergeInto("");
      setSelected(winner);
    },
  });

  const total = flat.length;
  // 按 id 从最新的树里取子树：拆/合之后 `selected` 是接口返回的单条条款，不带 children。
  const selectedNode = flat.find((node) => node.id === selected?.id);
  const descendantIds = new Set(flatten(selectedNode?.children ?? []).map((node) => node.id));

  useEffect(() => {
    if (!clauses.data) return;
    const nodes = flatten(clauses.data);
    const hashClauseId = location.hash.match(/^#clause-(\d+)$/)?.[1];
    const hashClause = hashClauseId
      ? nodes.find((node) => node.id === Number(hashClauseId))
      : undefined;
    setSelected((current) => {
      const next = hashClause ?? (current && nodes.some((node) => node.id === current.id) ? current : nodes[0]) ?? null;
      return current?.id === next?.id ? current : next;
    });
  }, [clauses.data, location.hash]);

  useEffect(() => {
    if (selected) {
      requestAnimationFrame(() =>
        document.getElementById(`clause-${selected.id}`)?.scrollIntoView({ block: "start" }),
      );
    }
  }, [selected]);

  return (
    <section>
      <p className="kn-mb-4">
        <Link to="/documents" className="kn-check-label">
          ← {t("documents.title")}
        </Link>
      </p>

      <div className="kn-page-header">
        <div>
          <h2>{doc.data?.title}</h2>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 4 }}>
            {doc.data && (
              <span className="kn-badge kn-badge-emerald">{t(`documents.statuses.${doc.data.status}`)}</span>
            )}
            <span className="kn-badge">
              {t("documents.version")}: {doc.data?.version ?? "—"}
            </span>
            <span className="kn-badge kn-badge-blue">
              {t("documents.clauses")}: {total}
            </span>
            {doc.data?.owner && <span className="kn-badge">{t("documents.owner")}: {doc.data.owner}</span>}
          </div>
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {canEdit && doc.data?.status === "active" && (
            <button className="kn-btn-secondary kn-btn-sm" disabled={rebuildIndex.isPending} onClick={() => rebuildIndex.mutate()}>
              {t("documents.rebuildIndex")}
            </button>
          )}
          {canEdit && doc.data?.status === "parse_failed" && (
            <button className="kn-btn-secondary kn-btn-sm" disabled={reparse.isPending} onClick={() => reparse.mutate()}>
              {t("documents.reparse")}
            </button>
          )}
          {doc.data?.supersedes_id != null && (
            <Link
              to={`/documents/${id}/change-impact`}
              className="kn-btn-secondary kn-btn-sm"
              style={{ display: "inline-flex", alignItems: "center", textDecoration: "none" }}
            >
              {t("impact.viewImpact")}
            </Link>
          )}
          {/* 新版本只能接在现行版后面（后端也拒绝）：接在已替换或没生效过的文档后面，
              真正的现行版不会被撤下，两版同时生效。已有后继时后端会指出是哪一份。 */}
          {canEdit && doc.data?.status === "active" && (
            <button
              type="button"
              className="kn-btn-primary kn-btn-sm"
              onClick={() => setImporting(true)}
            >
              {t("impact.importNewVersion")}
            </button>
          )}
        </div>
      </div>

      {doc.error && <p role="alert">{doc.error.message}</p>}
      {clauses.error && <p role="alert">{clauses.error.message}</p>}
      {doc.data && parseErrorText(doc.data) && <p role="alert">{parseErrorText(doc.data)}</p>}
      {reparse.error && <p role="alert">{reparse.error.message}</p>}
      {rebuildIndex.error && <p role="alert">{rebuildIndex.error.message}</p>}
      {rebuildIndex.isSuccess && <p role="status">{t("documents.indexQueued")}</p>}

      {importing && (
        <div className="kn-card" style={{ marginBottom: 20, border: "1px solid var(--accent-blue)" }}>
          <h4 className="kn-m-0 kn-mb-2">{t("impact.importNewVersion")}</h4>
          <p className="kn-secondary kn-text-xs kn-mb-3">
            {t("documents.dropHint")}
          </p>
          <input type="hidden" name="supersedes_id" value={id ?? ""} />
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,.docx"
            aria-label={t("documents.upload")}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) uploadVersion.mutate(file);
              event.target.value = "";
            }}
          />
          {uploadVersion.isPending && <LoadingState inline />}
          {uploadVersion.error && (
            <p role="alert">
              <span>⚠️</span> {uploadVersion.error.message}
            </p>
          )}
          <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
            <button type="button" className="kn-btn-secondary" onClick={() => setImporting(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </div>
      )}

      {parseWarningText(doc.data?.parse_warnings, doc.data?.parse_warning_codes) && (
        <pre
          style={{
            background: "var(--accent-amber-bg)",
            border: "1px solid var(--accent-amber-border)",
            color: "var(--accent-amber)",
            padding: 12,
            whiteSpace: "pre-wrap",
            fontSize: "0.8125rem",
            marginBottom: 20,
          }}
        >
          ⚠️ {parseWarningText(doc.data?.parse_warnings, doc.data?.parse_warning_codes)}
        </pre>
      )}

      {!isDemoSession() && doc.data?.status === "active" && id && <DecisionScan documentId={id} canScan={canEdit} />}

      {!isDemoSession() && doc.data?.status === "active" && (
        <section className="kn-mb-5 kn-card" aria-label={t("documents.coverageTitle")}>
          <strong>{t("documents.coverageTitle")}</strong>
          <p>{t("documents.coverageExplanation")}</p>
          {coverage.error && <p role="alert">{coverage.error.message}</p>}
          {coverage.isPending && <LoadingState />}
          {coverage.data && <>
            <p>{t("documents.coverageCounts", coverage.data.counts)}</p>
            {coverage.data.clauses.some((row) => row.inventory_status === "needs_review") && <div>
              <strong>{t("inventory.needsReviewTitle")}</strong>
              <ul>{coverage.data.clauses.filter((row) => row.inventory_status === "needs_review").map((row) => (
                <li key={row.clause_id}><button type="button" className="kn-btn-secondary" onClick={() => {
                  const clause = flat.find((node) => node.id === row.clause_id);
                  if (clause) setSelected(clause);
                }}>{row.citation_label}</button></li>
              ))}</ul>
            </div>}
            {!!coverage.data.reference_issues?.length && <div>
              <strong>{t("documents.referenceIssuesTitle")}</strong>
              <p>{t("documents.referenceIssuesExplanation")}</p>
              <ul className="kn-scroll-240">
                {coverage.data.reference_issues.map((issue) => (
                  <li key={`${issue.source_clause_id}:${issue.label}:${issue.status}`}>
                    <button type="button" className="kn-btn-secondary" onClick={() => {
                      const clause = flat.find((node) => node.id === issue.source_clause_id);
                      if (clause) setSelected(clause);
                    }}>
                      {flat.find((node) => node.id === issue.source_clause_id)?.citation_label} → {issue.label}
                      {" · "}{t(`documents.referenceStatus.${issue.status}`)}
                    </button>
                  </li>
                ))}
              </ul>
            </div>}
            <details>
              <summary>{t("documents.coverageInspect")}</summary>
              <ul className="kn-scroll-240">
                {coverage.data.clauses.filter((row) => row.status !== "cited").map((row) => (
                  <li key={row.clause_id}>
                    <button type="button" className="kn-btn-secondary" onClick={() => {
                      const clause = flat.find((node) => node.id === row.clause_id);
                      if (clause) setSelected(clause);
                    }}>
                      {row.citation_label} · {t(`documents.coverageStatus.${row.status}`)}
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          </>}
        </section>
      )}

      {/* Split Inspector Workspace */}
      {!isDemoSession() && id && <OriginalSources key={id} documentId={id} />}
      <div style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>
        {/* Navigation Tree Pane */}
        <nav
          className="kn-card"
          style={{
            flex: "0 0 320px",
            maxHeight: "75vh",
            overflow: "auto",
            padding: "16px 12px",
          }}
        >
          <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--text-tertiary)", textTransform: "uppercase", padding: "0 8px 10px 8px", letterSpacing: "0.06em" }}>
            {t("documents.outline")}
          </div>
          {clauses.data && (
            <Tree nodes={clauses.data} onSelect={setSelected} selected={selected?.id ?? null} />
          )}
        </nav>

        {/* Clause Reader Pane */}
        <article
          id={selected ? `clause-${selected.id}` : undefined}
          className="kn-card"
          style={{
            flex: 1,
            minHeight: 280,
            background: selected ? "var(--stage-card-hover)" : "var(--stage-card)",
            border: selected ? "1px solid var(--accent-blue)" : "1px solid var(--stage-border)",
            boxShadow: selected ? "var(--shadow-md)" : undefined,
          }}
        >
          {selected ? (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
                <h3 style={{ margin: 0, fontSize: "1.2rem" }}>{selected.heading}</h3>
                <div className="kn-row">
                  <code className="kn-text-xs">{selected.citation_label}</code>
                  {selected.page_ref && <span className="kn-badge">p.{selected.page_ref}</span>}
                </div>
              </div>
              <p style={{ color: "var(--text-tertiary)", fontSize: "0.8125rem", marginBottom: 16 }}>
                {selected.heading_path}
              </p>
              {/* 已替换的旧版是历史，条款不再拆合（后端也拒绝）：只读显示 */}
              {!isDemoSession() && id && <OriginalSources key={selected.id} documentId={id} clauseId={selected.id} />}
              {!isDemoSession() && id && <RequirementInventory
                key={`${selected.id}:${coverage.data?.clauses.find((row) => row.clause_id === selected.id)?.inventory_id}`}
                documentId={id} clauseId={selected.id}
                inventoryId={coverage.data?.clauses.find((row) => row.clause_id === selected.id)?.inventory_id ?? null}
                status={coverage.data?.clauses.find((row) => row.clause_id === selected.id)?.inventory_status ?? null}
              />}
              {canEdit && doc.data?.status !== "superseded" ? (
                <>
                  <p style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", marginBottom: 8 }}>
                    {t("documents.splitHint")}
                  </p>
                  <textarea
                    ref={bodyRef}
                    readOnly
                    defaultValue={selected.text}
                    key={selected.id + selected.text}
                    aria-label={selected.heading}
                    style={{
                      width: "100%",
                      minHeight: 200,
                      whiteSpace: "pre-wrap",
                      font: "inherit",
                      fontSize: "0.9375rem",
                      lineHeight: 1.6,
                      background: "var(--stage-card-subtle)",
                      border: "1px solid var(--stage-border)",
                      padding: 16,
                      borderRadius: "var(--radius-sm)",
                      color: "var(--text-primary)",
                      resize: "vertical",
                    }}
                  />
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 12, alignItems: "center" }}>
                    <button
                      type="button"
                      className="kn-btn-secondary kn-btn-sm"
                      onClick={() => {
                        const at = bodyRef.current?.selectionStart ?? 0;
                        split.mutate(at);
                      }}
                    >
                      {t("documents.split")}
                    </button>
                    <label style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: "0.8125rem" }}>
                      {t("documents.merge")}
                      <select
                        value={mergeInto}
                        onChange={(event) => setMergeInto(event.target.value)}
                        aria-label={t("documents.mergePick")}
                      >
                        <option value="">{t("documents.mergePick")}</option>
                        {/* 不能并进自己的下级条款：它的子条款会挂到胜者名下，树里出现环（后端也拒绝） */}
                        {flat
                          .filter((node) => node.id !== selected.id && !descendantIds.has(node.id))
                          .map((node) => (
                            <option key={node.id} value={node.id}>
                              {node.citation_label} {node.heading}
                            </option>
                          ))}
                      </select>
                    </label>
                    <button
                      type="button"
                      className="kn-btn-secondary kn-btn-sm"
                      disabled={!mergeInto}
                      onClick={() => merge.mutate(Number(mergeInto))}
                    >
                      {t("documents.merge")}
                    </button>
                  </div>
                  {(split.error || merge.error) && (
                    <p role="alert" className="kn-mt-2">
                      {(split.error ?? merge.error)?.message}
                    </p>
                  )}
                </>
              ) : (
                <pre
                  style={{
                    whiteSpace: "pre-wrap",
                    font: "inherit",
                    fontSize: "0.9375rem",
                    lineHeight: 1.6,
                    background: "var(--stage-card-subtle)",
                    border: "1px solid var(--stage-border)",
                    padding: 16,
                    borderRadius: "var(--radius-sm)",
                    color: "var(--text-primary)",
                  }}
                >
                  {selected.text}
                </pre>
              )}
            </>
          ) : (
            <div style={{ textAlign: "center", padding: "60px 20px", color: "var(--text-tertiary)" }}>
              <div className="kn-empty-emoji">📖</div>
              <EmptyState compact />
            </div>
          )}
        </article>
      </div>
    </section>
  );
}
