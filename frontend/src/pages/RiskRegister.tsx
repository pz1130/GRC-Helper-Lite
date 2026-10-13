import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { request } from "../api";
import { useAuth } from "../auth";

type RiskStatus = "open" | "mitigating" | "accepted" | "closed";

interface Risk {
  id: number;
  title: string;
  description: string;
  source: "gap" | "conflict" | "manual" | "audit_finding";
  source_ref: Record<string, unknown>;
  likelihood: number;
  impact: number;
  inherent_score: number;
  mitigation: string;
  residual_likelihood: number | null;
  residual_impact: number | null;
  residual_score: number | null;
  owner_user_id: number | null;
  due_date: string | null;
  status: RiskStatus;
}

interface Owner {
  id: number;
  name: string;
  email: string;
}

const scores = [1, 2, 3, 4, 5];
const statuses: RiskStatus[] = ["open", "mitigating", "accepted", "closed"];

function riskTone(score: number): { background: string; color: string } {
  if (score >= 17) return { background: "var(--accent-ruby)", color: "white" };
  if (score >= 10) return { background: "var(--accent-ruby-bg)", color: "var(--accent-ruby)" };
  if (score >= 5) return { background: "var(--accent-amber-bg)", color: "var(--accent-amber)" };
  return { background: "var(--accent-emerald-bg)", color: "var(--accent-emerald)" };
}

function RiskMatrix({ risks }: { risks: Risk[] }) {
  const { t } = useTranslation();
  const active = risks.filter((risk) => risk.status !== "closed");
  return (
    <div className="kn-card" style={{ overflowX: "auto" }}>
      <h3 style={{ marginTop: 0 }}>{t("risks.matrix")}</h3>
      <p style={{ color: "var(--text-secondary)", fontSize: "0.8125rem" }}>{t("risks.matrixHint")}</p>
      <table aria-label={t("risks.matrix")} style={{ tableLayout: "fixed", minWidth: 520 }}>
        <thead><tr><th>{t("risks.impactLikelihood")}</th>{scores.map((likelihood) => <th key={likelihood} style={{ textAlign: "center" }}>{likelihood}</th>)}</tr></thead>
        <tbody>
          {[5, 4, 3, 2, 1].map((impact) => (
            <tr key={impact}>
              <th style={{ textAlign: "center" }}>{impact}</th>
              {scores.map((likelihood) => {
                const score = impact * likelihood;
                const count = active.filter((risk) => risk.impact === impact && risk.likelihood === likelihood).length;
                return <td key={likelihood} style={{ textAlign: "center", padding: 6 }}><div style={{ ...riskTone(score), minHeight: 42, borderRadius: 7, display: "grid", placeItems: "center", fontWeight: 700 }} aria-label={t("risks.matrixCell", { impact, likelihood, count })}>{count || "·"}</div></td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RiskRow({ risk, owners, canWrite }: { risk: Risk; owners: Owner[]; canWrite: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<RiskStatus>(risk.status);
  const [owner, setOwner] = useState(risk.owner_user_id === null ? "" : String(risk.owner_user_id));
  const [dueDate, setDueDate] = useState(risk.due_date ?? "");
  const [mitigation, setMitigation] = useState(risk.mitigation);
  const [residualLikelihood, setResidualLikelihood] = useState(risk.residual_likelihood === null ? "" : String(risk.residual_likelihood));
  const [residualImpact, setResidualImpact] = useState(risk.residual_impact === null ? "" : String(risk.residual_impact));
  useEffect(() => {
    setStatus(risk.status);
    setOwner(risk.owner_user_id === null ? "" : String(risk.owner_user_id));
    setDueDate(risk.due_date ?? "");
    setMitigation(risk.mitigation);
    setResidualLikelihood(risk.residual_likelihood === null ? "" : String(risk.residual_likelihood));
    setResidualImpact(risk.residual_impact === null ? "" : String(risk.residual_impact));
  }, [risk]);
  const save = useMutation({
    mutationFn: () => request(`/api/risks/${risk.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        status,
        owner_user_id: owner ? Number(owner) : null,
        due_date: dueDate || null,
        mitigation,
        residual_likelihood: residualLikelihood ? Number(residualLikelihood) : null,
        residual_impact: residualImpact ? Number(residualImpact) : null,
      }),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["risks"] }),
  });
  const ownerName = owners.find((item) => item.id === risk.owner_user_id)?.name ?? t("risks.unassigned");

  return (
    <tr>
      <td style={{ minWidth: 250 }}>
        <strong>{risk.title}</strong>
        <div style={{ color: "var(--text-secondary)", fontSize: "0.75rem", marginTop: 5 }}>{risk.description}</div>
        <span className="kn-badge" style={{ marginTop: 7 }}>{t(`risks.sources.${risk.source}`)}</span>
      </td>
      <td><span style={{ ...riskTone(risk.inherent_score), display: "inline-block", minWidth: 34, padding: "6px 9px", textAlign: "center", borderRadius: 6, fontWeight: 700 }}>{risk.inherent_score}</span><small style={{ display: "block", marginTop: 4, color: "var(--text-tertiary)" }}>{risk.likelihood} × {risk.impact}</small></td>
      <td style={{ minWidth: 190 }}>
        {canWrite ? <select aria-label={`${risk.title} ${t("risks.owner")}`} value={owner} onChange={(event) => setOwner(event.target.value)}><option value="">{t("risks.unassigned")}</option>{owners.map((item) => <option key={item.id} value={item.id}>{item.name} ({item.email})</option>)}</select> : ownerName}
        {canWrite ? <input aria-label={`${risk.title} ${t("risks.dueDate")}`} type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} style={{ marginTop: 6 }} /> : <small style={{ display: "block", marginTop: 5 }}>{risk.due_date ?? "—"}</small>}
      </td>
      <td style={{ minWidth: 230 }}>
        {canWrite ? <textarea aria-label={`${risk.title} ${t("risks.mitigation")}`} rows={2} value={mitigation} onChange={(event) => setMitigation(event.target.value)} /> : (risk.mitigation || "—")}
        {canWrite && <div style={{ display: "flex", gap: 6, marginTop: 6 }}><select aria-label={`${risk.title} ${t("risks.residualLikelihood")}`} value={residualLikelihood} onChange={(event) => setResidualLikelihood(event.target.value)}><option value="">{t("risks.residualLikelihood")}</option>{scores.map((score) => <option key={score}>{score}</option>)}</select><select aria-label={`${risk.title} ${t("risks.residualImpact")}`} value={residualImpact} onChange={(event) => setResidualImpact(event.target.value)}><option value="">{t("risks.residualImpact")}</option>{scores.map((score) => <option key={score}>{score}</option>)}</select></div>}
        {!canWrite && risk.residual_score !== null && <small style={{ display: "block", marginTop: 5 }}>{t("risks.residualScore")}: {risk.residual_score}</small>}
      </td>
      <td style={{ minWidth: 145 }}>
        {canWrite ? <select aria-label={`${risk.title} ${t("risks.status")}`} value={status} onChange={(event) => setStatus(event.target.value as RiskStatus)}>{statuses.map((item) => <option key={item} value={item}>{t(`risks.statuses.${item}`)}</option>)}</select> : t(`risks.statuses.${risk.status}`)}
        {canWrite && <button className="kn-btn-sm" onClick={() => save.mutate()} disabled={save.isPending || Boolean(residualLikelihood) !== Boolean(residualImpact)} style={{ marginTop: 7, width: "100%" }}>{t("common.save")}</button>}
        {save.isSuccess && <small role="status" style={{ color: "var(--accent-emerald)" }}>{t("common.saved")}</small>}
        {save.error && <small role="alert" style={{ color: "var(--accent-ruby)" }}>{save.error.message}</small>}
      </td>
    </tr>
  );
}

export function RiskRegister() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const canWrite = user?.role === "admin" || user?.role === "grc_lead";
  const [filter, setFilter] = useState<"all" | RiskStatus>("all");
  const [title, setTitle] = useState("");
  const [likelihood, setLikelihood] = useState("3");
  const [impact, setImpact] = useState("3");
  const [owner, setOwner] = useState(user ? String(user.id) : "");
  const [dueDate, setDueDate] = useState("");
  const risks = useQuery({ queryKey: ["risks"], queryFn: () => request<Risk[]>("/api/risks") });
  const owners = useQuery({ queryKey: ["risk-owners"], queryFn: () => request<Owner[]>("/api/risks/owners") });
  const create = useMutation({
    mutationFn: () => request<Risk>("/api/risks", {
      method: "POST",
      body: JSON.stringify({ title, likelihood: Number(likelihood), impact: Number(impact), owner_user_id: owner ? Number(owner) : null, due_date: dueDate || null }),
    }),
    onSuccess: async () => {
      setTitle("");
      await queryClient.invalidateQueries({ queryKey: ["risks"] });
    },
  });
  const visible = (risks.data ?? []).filter((risk) => filter === "all" || risk.status === filter);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate();
  };

  return (
    <section>
      <div style={{ marginBottom: 24 }}><h2 style={{ marginBottom: 8 }}>{t("risks.title")}</h2><p style={{ margin: 0, color: "var(--text-secondary)" }}>{t("risks.subtitle")}</p></div>
      {canWrite && <form className="kn-card" onSubmit={submit} style={{ display: "grid", gridTemplateColumns: "minmax(220px, 1fr) 100px 100px minmax(150px, 0.7fr) 160px auto", gap: 10, alignItems: "end", marginBottom: 20 }}><label>{t("risks.riskTitle")}<input required value={title} onChange={(event) => setTitle(event.target.value)} /></label><label>{t("risks.likelihood")}<select value={likelihood} onChange={(event) => setLikelihood(event.target.value)}>{scores.map((score) => <option key={score}>{score}</option>)}</select></label><label>{t("risks.impact")}<select value={impact} onChange={(event) => setImpact(event.target.value)}>{scores.map((score) => <option key={score}>{score}</option>)}</select></label><label>{t("risks.owner")}<select value={owner} onChange={(event) => setOwner(event.target.value)}><option value="">{t("risks.unassigned")}</option>{owners.data?.map((item) => <option key={item.id} value={item.id}>{item.name} ({item.email})</option>)}</select></label><label>{t("risks.dueDate")}<input type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} /></label><button type="submit" disabled={create.isPending}>{t("risks.add")}</button>{create.error && <p role="alert" style={{ gridColumn: "1 / -1", color: "var(--accent-ruby)", margin: 0 }}>{create.error.message}</p>}</form>}
      {risks.isPending && <p role="status">{t("common.loading")}</p>}
      {risks.error && <p role="alert" style={{ color: "var(--accent-ruby)" }}>{risks.error.message}</p>}
      {risks.data && <div style={{ display: "grid", gridTemplateColumns: "minmax(420px, 0.85fr) minmax(520px, 1.15fr)", gap: 20 }}><RiskMatrix risks={risks.data} /><div className="kn-card"><div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 14 }}><h3 style={{ margin: 0 }}>{t("risks.list")}</h3><select aria-label={t("risks.statusFilter")} value={filter} onChange={(event) => setFilter(event.target.value as "all" | RiskStatus)} style={{ width: 170 }}><option value="all">{t("risks.allStatuses")}</option>{statuses.map((item) => <option key={item} value={item}>{t(`risks.statuses.${item}`)}</option>)}</select></div>{visible.length === 0 ? <p style={{ color: "var(--text-secondary)" }}>{t("risks.empty")}</p> : <div className="kn-table-container" style={{ margin: 0 }}><table><thead><tr><th>{t("risks.risk")}</th><th>{t("risks.inherentScore")}</th><th>{t("risks.ownerDue")}</th><th>{t("risks.mitigationResidual")}</th><th>{t("risks.status")}</th></tr></thead><tbody>{visible.map((risk) => <RiskRow key={risk.id} risk={risk} owners={owners.data ?? []} canWrite={canWrite} />)}</tbody></table></div>}</div></div>}
    </section>
  );
}
