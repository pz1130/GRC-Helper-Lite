import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { request } from "./api";

interface Block {
  id: number;
  text: string;
  origin: string;
  locator: {
    page?: number;
    document_line?: number;
    line?: number;
    body_index?: number;
    table?: number;
    positions?: { row: number; column: number }[];
  };
  clause_ids: number[];
}

interface SourcePage {
  available: boolean;
  total: number;
  unassigned: number;
  has_more: boolean;
  blocks: Block[];
}

export function OriginalSources({ documentId, clauseId }: { documentId: string; clauseId?: number }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [unassigned, setUnassigned] = useState(false);
  const limit = 25;
  const sources = useQuery({
    queryKey: ["source-blocks", documentId, clauseId, offset, unassigned],
    queryFn: () => request<SourcePage>(
      `${clauseId ? `/api/clauses/${clauseId}` : `/api/documents/${documentId}`}/source-blocks`
      + `?offset=${offset}&limit=${limit}&unassigned_only=${unassigned}`,
    ),
    enabled: open,
  });

  function position(block: Block) {
    const loc = block.locator;
    const parts: string[] = [];
    if (loc.page) parts.push(t("sources.page", { value: loc.page }));
    if (loc.document_line || loc.line) parts.push(t("sources.line", { value: loc.document_line ?? loc.line }));
    if (loc.body_index) parts.push(t("sources.position", { value: loc.body_index }));
    if (loc.table) parts.push(t("sources.table", { value: loc.table }));
    if (loc.positions) parts.push(loc.positions.map((cell) => t("sources.cell", cell)).join("; "));
    return parts.join(" · ");
  }

  return (
    <details className="kn-card" style={{ marginBottom: 16 }} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{t(clauseId ? "sources.clauseTitle" : "sources.documentTitle")}</summary>
      {open && <>
        <p>{t("sources.explanation")}</p>
        {sources.isPending && <p role="status">{t("common.loading")}</p>}
        {sources.error && <p role="alert">{sources.error.message}</p>}
        {sources.data && !sources.data.available && <p>{t("sources.legacy")}</p>}
        {sources.data?.available && <>
          <p>{t("sources.counts", { total: sources.data.total, unassigned: sources.data.unassigned })}</p>
          {!clauseId && <label>
            <input type="checkbox" checked={unassigned} onChange={(event) => {
              setUnassigned(event.target.checked); setOffset(0);
            }} /> {t("sources.onlyUnassigned")}
          </label>}
          {sources.data.blocks.length === 0 && <p>{t("sources.empty")}</p>}
          {sources.data.blocks.map((block) => (
            <div key={block.id} style={{ borderTop: "1px solid var(--stage-border)", padding: "12px 0" }}>
              <small>{t(`sources.origin.${block.origin}`, { defaultValue: block.origin })} · {position(block)}</small>
              <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", font: "inherit" }}>{block.text}</pre>
            </div>
          ))}
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className="kn-btn-secondary" disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - limit))}>{t("sources.previous")}</button>
            <button type="button" className="kn-btn-secondary" disabled={!sources.data.has_more}
              onClick={() => setOffset(offset + limit)}>{t("sources.next")}</button>
          </div>
        </>}
      </>}
    </details>
  );
}
