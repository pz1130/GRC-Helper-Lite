import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { ApiError, request } from "../api";
import { Card, DataTable, LoadingState, PageHeader } from "../components/ui";

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
      <p className="kn-mb-4">
        <Link to={`/documents/${id}`} className="kn-check-label">
          ← {t("documents.title")}
        </Link>
      </p>

      <PageHeader
        title={t("impact.title")}
        subtitle={data?.previous_document ? `${data.previous_document.title}${data.previous_document.version ? ` · ${data.previous_document.version}` : ""}` : undefined}
      />

      {impact.isPending && <LoadingState />}

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
            <p role="alert" className="kn-card kn-card--warn">
              {t("impact.parserGenerationMismatch")}
            </p>
          )}
          <div className="kn-stack-5 kn-mb-6">
            <section data-impact-section="added" className="kn-card">
              <h3 className="kn-mt-0">{t("impact.added")}</h3>
              <ul>
                {data.added.map((clause) => (
                  <li key={clause.clause_id}>
                    <code>{clause.citation_label}</code> {clause.text}
                  </li>
                ))}
              </ul>
            </section>

            <section data-impact-section="removed" className="kn-card">
              <h3 className="kn-mt-0">{t("impact.removed")}</h3>
              <ul>
                {data.removed.map((clause) => (
                  <li key={clause.clause_id}>
                    <code>{clause.citation_label}</code> {clause.text}
                  </li>
                ))}
              </ul>
            </section>

            <section data-impact-section="matched" className="kn-card">
              <h3 className="kn-mt-0">{t("impact.matched")}</h3>
              <ul>
                {data.matched.map((clause) => (
                  <li key={`${clause.old_clause_id}-${clause.new_clause_id}`}>
                    <code>{clause.citation_label}</code>
                    {clause.changed ? (
                      <details className="kn-mt-1">
                        <summary>{t("impact.textChanged")}</summary>
                        <div className="kn-mt-1">
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

          <Card className="kn-mb-5" title={t("impact.affectedControls")}>
            <DataTable className="kn-m-0" head={<><tr>
                    <th>{t("controls.code")}</th>
                    <th>{t("controls.name")}</th>
                  </tr></>}>
                  {data.affected_controls.map((control) => (
                    <tr key={control.id}>
                      <td><code>{control.code}</code></td>
                      <td>{control.title}</td>
                    </tr>
                  ))}
                </DataTable>
          </Card>

          <Card className="kn-mb-5" title={t("impact.affectedMappings")}>
            <DataTable className="kn-m-0" head={<><tr>
                    <th>{t("controls.code")}</th>
                    <th>{t("mapping.frameworkItem")}</th>
                  </tr></>}>
                  {data.affected_mappings.map((mapping) => (
                    <tr key={mapping.id}>
                      <td><code>{mapping.control_code}</code></td>
                      <td><code>{mapping.framework_item_code}</code></td>
                    </tr>
                  ))}
                </DataTable>
          </Card>

          <Card title={t("impact.affectedEvidence")}>
            <DataTable className="kn-m-0" head={<><tr>
                    <th>{t("evidence.control")}</th>
                    <th>{t("evidence.titleField")}</th>
                  </tr></>}>
                  {data.affected_evidence.map((item) => (
                    <tr key={item.id}>
                      <td><code>{item.control_code}</code></td>
                      <td>{item.title}</td>
                    </tr>
                  ))}
                </DataTable>
          </Card>
        </>
      )}
    </section>
  );
}
