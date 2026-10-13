import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { ApiError, request } from "../api";

interface PreviousDocument {
  id: number;
  title: string;
  version: string | null;
}

interface ClauseChange {
  clause_id: number;
  citation_label: string;
  text: string;
}

interface MatchedClause {
  old_clause_id: number;
  new_clause_id: number;
  citation_label: string;
  text: string;
  old_text: string;
  changed: boolean;
}

interface AffectedControl {
  id: number;
  code: string;
  title: string;
}

interface AffectedMapping {
  id: number;
  control_id: number;
  control_code: string;
  framework_item_code: string;
}

interface AffectedEvidence {
  id: number;
  control_id: number;
  control_code: string;
  title: string;
}

interface Impact {
  previous_document: PreviousDocument;
  parser_generation_mismatch: boolean;
  added: ClauseChange[];
  removed: ClauseChange[];
  matched: MatchedClause[];
  affected_controls: AffectedControl[];
  affected_mappings: AffectedMapping[];
  affected_evidence: AffectedEvidence[];
}

export function ChangeImpact() {
  const { t } = useTranslation();
  const { id } = useParams();

  const impact = useQuery({
    queryKey: ["change-impact", id],
    queryFn: () => request<Impact>(`/api/documents/${id}/change-impact`),
    enabled: Boolean(id),
    retry: (count, error) => !(error instanceof ApiError && error.status === 400) && count < 2,
  });

  const noPrevious = impact.error instanceof ApiError && impact.error.status === 400;
  const data = impact.data;

  return (
    <section>
      <p style={{ marginBottom: 16 }}>
        <Link to={`/documents/${id}`} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.875rem" }}>
          ← {t("documents.title")}
        </Link>
      </p>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
        <div>
          <h2>{t("impact.title")}</h2>
          {data?.previous_document && (
            <p style={{ margin: "4px 0 0", fontSize: "0.875rem", color: "var(--text-secondary)" }}>
              {data.previous_document.title}
              {data.previous_document.version ? ` · ${data.previous_document.version}` : ""}
            </p>
          )}
        </div>
      </div>

      {impact.isPending && <p role="status">{t("common.loading")}</p>}

      {noPrevious && (
        <p role="status">{t("impact.noPrevious")}</p>
      )}

      {impact.error && !noPrevious && (
        <p role="alert">
          <span>⚠️</span> {impact.error.message}{" "}
          <button className="kn-btn-sm" onClick={() => void impact.refetch()}>{t("common.retry")}</button>
        </p>
      )}

      {data && (
        <>
          {data.parser_generation_mismatch && (
            <p role="alert" className="kn-card" style={{ borderColor: "var(--accent-amber)" }}>
              {t("impact.parserGenerationMismatch")}
            </p>
          )}
          <div style={{ display: "grid", gap: 20, marginBottom: 24 }}>
            <section data-impact-section="added" className="kn-card">
              <h3 style={{ marginTop: 0 }}>{t("impact.added")}</h3>
              <ul>
                {data.added.map((clause) => (
                  <li key={clause.clause_id}>
                    <code>{clause.citation_label}</code> {clause.text}
                  </li>
                ))}
              </ul>
            </section>

            <section data-impact-section="removed" className="kn-card">
              <h3 style={{ marginTop: 0 }}>{t("impact.removed")}</h3>
              <ul>
                {data.removed.map((clause) => (
                  <li key={clause.clause_id}>
                    <code>{clause.citation_label}</code> {clause.text}
                  </li>
                ))}
              </ul>
            </section>

            <section data-impact-section="matched" className="kn-card">
              <h3 style={{ marginTop: 0 }}>{t("impact.matched")}</h3>
              <ul>
                {data.matched.map((clause) => (
                  <li key={`${clause.old_clause_id}-${clause.new_clause_id}`}>
                    <code>{clause.citation_label}</code>
                    {clause.changed ? (
                      <details style={{ marginTop: 6 }}>
                        <summary>{t("impact.textChanged")}</summary>
                        <div style={{ marginTop: 6 }}>
                          <div><strong>{t("impact.before")}</strong> {clause.old_text}</div>
                          <div><strong>{t("impact.after")}</strong> {clause.text}</div>
                        </div>
                      </details>
                    ) : clause.text ? ` ${clause.text}` : ""}
                  </li>
                ))}
              </ul>
            </section>
          </div>

          <div className="kn-card" style={{ marginBottom: 20 }}>
            <h3 style={{ marginTop: 0 }}>{t("impact.affectedControls")}</h3>
            <div className="kn-table-container" style={{ margin: 0 }}>
              <table>
                <thead>
                  <tr>
                    <th>{t("controls.code")}</th>
                    <th>{t("controls.name")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.affected_controls.map((control) => (
                    <tr key={control.id}>
                      <td><code>{control.code}</code></td>
                      <td>{control.title}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="kn-card" style={{ marginBottom: 20 }}>
            <h3 style={{ marginTop: 0 }}>{t("impact.affectedMappings")}</h3>
            <div className="kn-table-container" style={{ margin: 0 }}>
              <table>
                <thead>
                  <tr>
                    <th>{t("controls.code")}</th>
                    <th>{t("mapping.frameworkItem")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.affected_mappings.map((mapping) => (
                    <tr key={mapping.id}>
                      <td><code>{mapping.control_code}</code></td>
                      <td><code>{mapping.framework_item_code}</code></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="kn-card">
            <h3 style={{ marginTop: 0 }}>{t("impact.affectedEvidence")}</h3>
            <div className="kn-table-container" style={{ margin: 0 }}>
              <table>
                <thead>
                  <tr>
                    <th>{t("evidence.control")}</th>
                    <th>{t("evidence.titleField")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.affected_evidence.map((item) => (
                    <tr key={item.id}>
                      <td><code>{item.control_code}</code></td>
                      <td>{item.title}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
