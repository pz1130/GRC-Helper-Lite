import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { request } from "./api";

interface Inventory {
  semantic_review?: { findings: {
    kind: string; clause_ids: number[]; requirement_ids: string[]; control_indexes: number[];
    detail: string; citations: { clause_id: number; quote: string }[];
  }[] } | null;
  normalized_controls?: { statement: string }[];
  requirements: {
    id: string; statement: string; target_clause_ids: number[];
    components: Record<string, string | null>;
    citations: { clause_id: number; quote: string }[];
  }[];
}

export function RequirementInventory({ documentId, clauseId, inventoryId, status }: {
  documentId: string; clauseId: number; inventoryId: number | null; status: string | null;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const inventory = useQuery({
    queryKey: ["requirement-inventory", documentId, inventoryId],
    queryFn: () => request<Inventory>(`/api/extraction/documents/${documentId}/inventories/${inventoryId}`),
    enabled: open && inventoryId !== null,
    refetchInterval: (query) => open && !query.state.data?.semantic_review ? 10000 : false,
  });
  return <details className="kn-card" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{t("inventory.title")}</summary>
    <p>{t("inventory.explanation")}</p>
    {inventoryId === null ? <p>{t("inventory.unavailable")}</p> : <>
      {status && <p>{t(`inventory.status.${status}`)}</p>}
      {inventory.isPending && <p role="status">{t("common.loading")}</p>}
      {inventory.error && <p role="alert">{inventory.error.message}</p>}
      {inventory.data && <section>
        <strong>{t("inventory.semanticTitle")}</strong>
        <p>{t("inventory.semanticExplanation")}</p>
        {!inventory.data.semantic_review ? <p>{t("inventory.semanticPending")}</p> : <>
          {!inventory.data.semantic_review.findings.some((finding) => finding.clause_ids.includes(clauseId)) &&
            <p>{t("inventory.semanticNoFindings")}</p>}
          {inventory.data.semantic_review.findings.filter((finding) => finding.clause_ids.includes(clauseId)).map((finding, index) => (
            <article key={index}>
              <strong>{t(`inventory.findingKind.${finding.kind}`)}</strong>
              <p>{finding.detail}</p>
              {finding.requirement_ids.length > 0 && <p>{finding.requirement_ids.join(", ")}</p>}
              {finding.control_indexes.map((controlIndex) => <p key={controlIndex}>
                {t("inventory.proposedControl")}: {inventory.data?.normalized_controls?.[controlIndex]?.statement}
              </p>)}
              {finding.citations.map((citation, quoteIndex) => <blockquote key={quoteIndex}>{citation.quote}</blockquote>)}
            </article>
          ))}
        </>}
      </section>}
      {inventory.data && <ul>
        {inventory.data.requirements.filter((item) => item.target_clause_ids.includes(clauseId)).map((item) => (
          <li key={item.id}>
            <strong>{item.id}</strong><p>{item.statement}</p>
            <dl>{Object.entries(item.components).map(([name, value]) => <div key={name}>
              <dt>{t(`inventory.components.${name}`)}</dt>
              <dd>{value ?? t("inventory.notStated")}</dd>
            </div>)}</dl>
            {item.citations.map((citation, index) => <blockquote key={index}>{citation.quote}</blockquote>)}
          </li>
        ))}
      </ul>}
    </>}
  </details>;
}
