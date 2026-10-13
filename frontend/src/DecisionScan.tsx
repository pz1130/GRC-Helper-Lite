import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { request } from "./api";

interface ScanResult {
  mode: "rules" | "model_assisted";
  reasons: string[];
  clause_count: number;
  model_count: number;
  rule_fallback_count: number;
  requirements: number;
  unlinked_requirements: number;
  model: string | null;
  scanned_at: string | null;
  rows: { clause_id: number; citation_label: string; heading_path: string; probability: number | null; rule_match: boolean; has_control: boolean }[];
}

export function DecisionScan({ documentId, canScan }: { documentId: string; canScan: boolean }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const key = ["requirement-scan", documentId];
  const scan = useQuery({
    queryKey: key,
    queryFn: async () => {
      const result = await request<ScanResult>(`/api/documents/${documentId}/requirement-scan`);
      if (!Array.isArray(result?.reasons) || !Array.isArray(result?.rows)) throw new Error(t("decision.invalidResponse"));
      return result;
    },
    refetchInterval: 10000,
  });
  const run = useMutation({
    mutationFn: () => request<ScanResult>(`/api/documents/${documentId}/requirement-scan`, { method: "POST" }),
    onSuccess: (data) => client.setQueryData(key, data),
    onError: () => { void client.invalidateQueries({ queryKey: key }); },
  });
  const data = scan.data;
  const blocked = !data || data.reasons.some((r) => ["disabled", "not_configured", "provider_unavailable", "document_inactive"].includes(r));
  return <section className="kn-card" style={{ marginBottom: 20 }} aria-label={t("decision.scanTitle")}>
    <h3>{t("decision.scanTitle")}</h3>
    <p>{t("decision.scanHint")}</p>
    {scan.isPending && <p role="status">{t("common.loading")}</p>}
    {scan.error && <p role="alert">{scan.error.message}</p>}
    {data && <>
      <p><strong>{t(`decision.mode.${data.mode}`)}</strong>{data.model ? ` · ${data.model}` : ""}</p>
      {data.reasons.map((reason) => <p key={reason} role="status">{t(`decision.reason.${reason}`)}</p>)}
      <p>{t("decision.counts", { total: data.clause_count, model: data.model_count, rules: data.rule_fallback_count, uncovered: data.unlinked_requirements })}</p>
      <p>{t("decision.limitations")}</p>
      {canScan && <>
        <p>{t("decision.sendNotice")}</p>
        <button className="kn-btn-secondary" disabled={blocked || run.isPending} onClick={() => run.mutate()}>
          {t(run.isPending ? "decision.running" : "decision.run")}
        </button>
      </>}
      <details style={{ marginTop: 12 }}>
        <summary>{t("decision.inspect")}</summary>
        {data.rows.filter((row) => !row.has_control).map((row) => <p key={row.clause_id}>
          <a href={`#clause-${row.clause_id}`}>{row.citation_label || row.heading_path}</a>
          {" · "}{t(row.rule_match ? "decision.ruleHit" : "decision.modelHit")}
          {row.probability !== null && ` · ${t("decision.probability", { value: Math.round(row.probability * 100) })}`}
        </p>)}
      </details>
    </>}
    {run.error && <p role="alert">{t("decision.requestFailed")} {run.error.message}</p>}
  </section>;
}
