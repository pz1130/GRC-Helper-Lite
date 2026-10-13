import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { request } from "../../api";
import type { GraphEdgeData, GraphNodeData } from "./types";

interface ControlSource {
  clause_id: number;
  document_id: number;
  document_title: string;
  citation_label: string;
  heading_path: string;
  relation: string;
}

interface ControlDetail {
  id: number;
  code: string;
  title: string;
  statement: string;
  sources: ControlSource[];
}

interface ClauseDetail {
  id: number;
  document_id: number;
  citation_label: string;
  heading_path: string;
  text: string;
}

function ClauseText({ clauseId }: { clauseId: number }) {
  const { data } = useQuery({
    queryKey: ["clause", clauseId],
    queryFn: () => request<ClauseDetail>(`/api/clauses/${clauseId}`),
  });
  if (!data) return null;
  return (
    <p style={{ fontSize: "0.8125rem", lineHeight: 1.6, color: "var(--text-secondary)" }}>
      {data.text}
    </p>
  );
}

export interface DrawerProps {
  node: GraphNodeData | null;
  edge: GraphEdgeData | null;
  onClose: () => void;
}

export function Drawer({ node, edge, onClose }: DrawerProps) {
  const { t } = useTranslation();
  const controlId =
    node && node.kind === "control" ? Number(node.key.split(":")[1]) : null;
  const { data: control } = useQuery({
    queryKey: ["control", controlId],
    queryFn: () => request<ControlDetail>(`/api/controls/${controlId}`),
    enabled: controlId !== null,
  });

  if (!node && !edge) return null;

  return (
    <aside
      role="complementary"
      aria-label={t("graph.drawer.title")}
      className="kn-graph-drawer"
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            className="kn-dot"
            style={{
              backgroundColor: edge
                ? "var(--accent-purple)"
                : node?.is_gap
                ? "var(--accent-ruby)"
                : "var(--accent-blue)",
            }}
          />
          <span style={{ fontSize: "0.75rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-tertiary)" }}>
            {edge ? t("graph.drawer.title") : node?.kind === "framework_item" ? t("graph.filters.framework") : t("controls.title")}
          </span>
        </div>
        <button
          type="button"
          className="kn-btn-secondary kn-btn-sm"
          onClick={onClose}
          style={{ padding: "4px 12px", fontSize: "0.75rem" }}
        >
          {t("common.close")}
        </button>
      </div>

      {edge && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div>
            <h2 style={{ fontSize: "1.125rem", margin: "0 0 6px" }}>{t(`graph.edgeKind.${edge.kind}`, edge.kind)}</h2>
            {edge.confidence !== null && (
              <span className="kn-badge kn-badge-blue" style={{ fontSize: "0.75rem" }}>
                {edge.confidence.toFixed(2)}
              </span>
            )}
          </div>

          {edge.rationale && (
            <div
              style={{
                background: "var(--stage-card-subtle)",
                border: "1px solid var(--stage-border)",
                borderRadius: "var(--radius-sm)",
                padding: "12px 14px",
              }}
            >
              <p style={{ fontSize: "0.8125rem", margin: 0, lineHeight: 1.5, color: "var(--text-primary)" }}>
                {edge.rationale}
              </p>
            </div>
          )}

          {edge.conflict_count > 0 && (
            <div
              style={{
                background: "var(--accent-ruby-bg)",
                border: "1px solid var(--accent-ruby-border)",
                borderRadius: "var(--radius-sm)",
                padding: "10px 14px",
              }}
            >
              <p style={{ fontSize: "0.8125rem", color: "var(--accent-ruby)", margin: 0, fontWeight: 600 }}>
                ⚠️ {t("graph.conflictPoints", { count: edge.conflict_count })}
              </p>
            </div>
          )}

          {edge.proposal_id !== null && (
            <Link
              to={
                edge.kind === "full" || edge.kind === "partial" || edge.kind === "supporting"
                  ? `/review?kind=mapping#proposal-${edge.proposal_id}`
                  : `/review?kind=relation#proposal-${edge.proposal_id}`
              }
              className="kn-btn kn-btn-secondary kn-btn-sm"
              style={{ alignSelf: "flex-start", marginTop: 4 }}
            >
              {t("graph.drawer.toReview")} →
            </Link>
          )}
        </div>
      )}

      {/* 框架项 */}
      {node && node.kind === "framework_item" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div>
            <h2 style={{ fontSize: "1.25rem", margin: "0 0 4px" }}>{node.code}</h2>
            <p style={{ fontWeight: 600, color: "var(--text-primary)", margin: 0, fontSize: "0.9375rem" }}>
              {node.title}
            </p>
          </div>
          {node.is_gap && (
            <div
              style={{
                background: "var(--accent-ruby-bg)",
                border: "1px solid var(--accent-ruby-border)",
                borderRadius: "var(--radius-sm)",
                padding: "10px 14px",
              }}
            >
              <p style={{ fontSize: "0.8125rem", color: "var(--accent-ruby)", margin: 0, fontWeight: 600 }}>
                {t("graph.drawer.notCovered")}
              </p>
            </div>
          )}
        </div>
      )}

      {/* 控制点 */}
      {node && control && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <h2 style={{ fontSize: "1.25rem", margin: "0 0 4px" }}>{control.code}</h2>
            <p style={{ fontWeight: 600, color: "var(--text-primary)", margin: "4px 0 0" }}>{control.title}</p>
          </div>

          <div
            style={{
              background: "var(--stage-card-subtle)",
              border: "1px solid var(--stage-border)",
              borderLeft: "3px solid var(--accent-blue)",
              borderRadius: "var(--radius-sm)",
              padding: "12px 14px",
            }}
          >
            <p style={{ fontSize: "0.8125rem", margin: 0, lineHeight: 1.5, color: "var(--text-primary)" }}>
              {control.statement}
            </p>
          </div>

          {control.sources.length > 0 && (
            <div>
              <p style={{ fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-tertiary)", fontWeight: 600, margin: "0 0 8px" }}>
                {t("controls.sources")}
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {control.sources.map((source) => (
                  <div
                    key={source.clause_id}
                    style={{
                      background: "var(--stage-card-subtle)",
                      border: "1px solid var(--stage-border)",
                      borderRadius: "var(--radius-sm)",
                      padding: "10px 12px",
                      display: "flex",
                      flexDirection: "column",
                      gap: 6,
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <code style={{ fontSize: "0.75rem", fontWeight: 600 }}>{source.citation_label}</code>
                      <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)" }}>{source.relation}</span>
                    </div>
                    <ClauseText clauseId={source.clause_id} />
                    <Link
                      to={`/documents/${source.document_id}#clause-${source.clause_id}`}
                      style={{ fontSize: "0.8125rem", alignSelf: "flex-start", marginTop: 2 }}
                    >
                      {t("graph.drawer.openInDocument")} →
                    </Link>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
