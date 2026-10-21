import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { request } from "../api";
import { PrerequisiteNotice } from "../components/PrerequisiteNotice";
import { useAuth } from "../auth";
import { Card, DataTable, EmptyState, LoadingState, PageHeader } from "../components/ui";

interface Framework {
  id: number;
  name_zh: string;
  name_en: string;
  version: string;
}

interface Assessment {
  id: number;
  framework_id: number;
  name: string;
  as_of_date: string;
  status: "draft" | "final";
}

interface Aggregate {
  total_items: number;
  scored_items: number;
  doc_average: number | null;
  impl_average: number | null;
}

interface Group extends Aggregate {
  framework_item_id: number;
  code: string;
  title: string;
}

interface MaturityItem {
  framework_item_id: number;
  parent_id: number | null;
  group_id: number;
  code: string;
  title: string;
  doc_score: number | null;
  impl_score: number | null;
  doc_rationale: string;
  impl_rationale: string;
}

interface Summary {
  assessment_id: number;
  framework_id: number;
  overall: Aggregate;
  groups: Group[];
  items: MaturityItem[];
}

interface RiskRef {
  id: number;
  source: string;
  source_ref: Record<string, unknown>;
}

interface SuggestQueued {
  job_id: string;
}

function formatScore(value: number | null): string {
  return value === null ? "—" : value.toFixed(2);
}

function scoreTone(value: number | null): { background: string; color: string } {
  if (value === null) return { background: "var(--stage-card-subtle)", color: "var(--text-tertiary)" };
  if (value < 1) return { background: "var(--accent-ruby-bg)", color: "var(--accent-ruby)" };
  if (value < 2) return { background: "var(--accent-amber-bg)", color: "var(--accent-amber)" };
  if (value < 3) return { background: "var(--accent-purple-bg)", color: "var(--accent-purple)" };
  if (value < 4) return { background: "var(--accent-cyan-bg)", color: "var(--accent-cyan)" };
  return { background: "var(--accent-emerald-bg)", color: "var(--accent-emerald)" };
}

function RadarChart({ groups }: { groups: Group[] }) {
  const { t } = useTranslation();
  const rated = groups.filter((group) => group.scored_items > 0);
  if (rated.length < 3) {
    return (
      <div aria-label={t("maturity.comparison")}>
        {rated.map((group) => (
          <div key={group.framework_item_id} className="kn-mb-4">
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 6 }}>
              <strong className="kn-text-xs">{group.code} {group.title}</strong>
              <span style={{ color: "var(--text-secondary)", fontSize: "0.75rem" }}>
                {formatScore(group.doc_average)} / {formatScore(group.impl_average)}
              </span>
            </div>
            <div className="kn-progress-bar" style={{ marginBottom: 5 }}>
              <div className="kn-progress-fill" style={{ width: `${((group.doc_average ?? 0) / 4) * 100}%`, background: "var(--accent-blue)" }} />
            </div>
            <div className="kn-progress-bar">
              <div className="kn-progress-fill" style={{ width: `${((group.impl_average ?? 0) / 4) * 100}%`, background: "var(--accent-emerald)" }} />
            </div>
          </div>
        ))}
        {rated.length === 0 && <p className="kn-secondary">{t("maturity.noScores")}</p>}
      </div>
    );
  }

  const size = 360;
  const center = size / 2;
  const radius = 116;
  const point = (index: number, value: number) => {
    const angle = (Math.PI * 2 * index) / rated.length - Math.PI / 2;
    const distance = radius * (value / 4);
    return `${center + Math.cos(angle) * distance},${center + Math.sin(angle) * distance}`;
  };
  const ring = (value: number) => rated.map((_, index) => point(index, value)).join(" ");

  return (
    <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label={t("maturity.radarLabel")} style={{ width: "100%", maxHeight: 390 }}>
      {[1, 2, 3, 4].map((value) => (
        <polygon key={value} points={ring(value)} fill="none" stroke="var(--stage-border)" strokeWidth="1" />
      ))}
      {rated.map((group, index) => {
        const outer = point(index, 4).split(",");
        const label = point(index, 4.65).split(",");
        return (
          <g key={group.framework_item_id}>
            <line x1={center} y1={center} x2={outer[0]} y2={outer[1]} stroke="var(--stage-border)" />
            <text x={label[0]} y={label[1]} textAnchor="middle" dominantBaseline="central" fill="var(--text-secondary)" fontSize="11">
              {group.code.length > 12 ? group.code.slice(0, 12) : group.code}
            </text>
          </g>
        );
      })}
      <polygon points={rated.map((group, index) => point(index, group.doc_average ?? 0)).join(" ")} fill="var(--accent-blue-glow)" stroke="var(--accent-blue)" strokeWidth="2" className="fx-chart-line" pathLength={1} />
      <polygon points={rated.map((group, index) => point(index, group.impl_average ?? 0)).join(" ")} fill="var(--accent-emerald-bg)" stroke="var(--accent-emerald)" strokeWidth="2" className="fx-chart-line" pathLength={1} />
    </svg>
  );
}

function GapRiskButton({ item, assessment }: { item: MaturityItem; assessment: Assessment }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const risks = useQuery({ queryKey: ["risks"], queryFn: () => request<RiskRef[]>("/api/risks") });
  const existing = risks.data?.find((risk) => risk.source === "gap" && Number(risk.source_ref.assessment_id) === assessment.id && Number(risk.source_ref.framework_item_id) === item.framework_item_id);
  const create = useMutation({
    mutationFn: () => request("/api/risks/from-maturity-gap", {
      method: "POST",
      body: JSON.stringify({ assessment_id: assessment.id, framework_item_id: item.framework_item_id }),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["risks"] }),
  });
  if (item.impl_score === null || item.impl_score >= 3) return null;
  if (existing) return <Link to="/risks" style={{ display: "inline-block", marginTop: 7, fontSize: "0.75rem" }}>{t("maturity.viewRisk")}</Link>;
  return (
    <>
      <button className="kn-btn-sm" onClick={() => create.mutate()} disabled={create.isPending || risks.isPending} style={{ marginTop: 7 }}>{t("maturity.createRisk")}</button>
      {create.isSuccess && <small role="status" className="kn-block kn-ok-text kn-mt-1">{t("maturity.riskCreated")}</small>}
      {create.error && <small role="alert" className="kn-block kn-danger-text kn-mt-1">{create.error.message}</small>}
    </>
  );
}

function ScoreEditor({ item, assessment, canWrite }: { item: MaturityItem; assessment: Assessment; canWrite: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [docScore, setDocScore] = useState(item.doc_score === null ? "" : String(item.doc_score));
  const [implScore, setImplScore] = useState(item.impl_score === null ? "" : String(item.impl_score));
  const [docRationale, setDocRationale] = useState(item.doc_rationale);
  const [implRationale, setImplRationale] = useState(item.impl_rationale);
  useEffect(() => {
    setDocScore(item.doc_score === null ? "" : String(item.doc_score));
    setImplScore(item.impl_score === null ? "" : String(item.impl_score));
    setDocRationale(item.doc_rationale);
    setImplRationale(item.impl_rationale);
  }, [item]);
  const save = useMutation({
    mutationFn: () => request(`/api/maturity/assessments/${assessment.id}/scores`, {
      method: "PUT",
      body: JSON.stringify({
        framework_item_id: item.framework_item_id,
        doc_score: Number(docScore),
        impl_score: Number(implScore),
        doc_rationale: docRationale,
        impl_rationale: implRationale,
      }),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["maturity-summary", assessment.id] }),
  });
  const locked = !canWrite || assessment.status === "final";

  return (
    <tr>
      <td style={{ minWidth: 230 }}><code>{item.code}</code><div style={{ marginTop: 5 }}>{item.title}</div></td>
      <td>
        {locked ? <span style={{ ...scoreTone(item.doc_score), display: "inline-block", minWidth: 28, padding: "5px 9px", borderRadius: 6, textAlign: "center", fontWeight: 700 }}>{item.doc_score ?? "—"}</span> : (
          <select aria-label={`${item.code} ${t("maturity.docScore")}`} value={docScore} onChange={(event) => setDocScore(event.target.value)}>
            <option value="">—</option>{[0, 1, 2, 3, 4].map((score) => <option key={score}>{score}</option>)}
          </select>
        )}
      </td>
      <td>
        {locked ? <span style={{ ...scoreTone(item.impl_score), display: "inline-block", minWidth: 28, padding: "5px 9px", borderRadius: 6, textAlign: "center", fontWeight: 700 }}>{item.impl_score ?? "—"}</span> : (
          <select aria-label={`${item.code} ${t("maturity.implScore")}`} value={implScore} onChange={(event) => setImplScore(event.target.value)}>
            <option value="">—</option>{[0, 1, 2, 3, 4].map((score) => <option key={score}>{score}</option>)}
          </select>
        )}
      </td>
      <td style={{ minWidth: 260 }}>
        {locked ? (
          <small className="kn-secondary">{item.doc_rationale || item.impl_rationale || t("maturity.noRationale")}</small>
        ) : (
          <div className="kn-stack-1">
            <input aria-label={`${item.code} ${t("maturity.docRationale")}`} value={docRationale} onChange={(event) => setDocRationale(event.target.value)} placeholder={t("maturity.docRationale")} />
            <input aria-label={`${item.code} ${t("maturity.implRationale")}`} value={implRationale} onChange={(event) => setImplRationale(event.target.value)} placeholder={t("maturity.implRationale")} />
          </div>
        )}
      </td>
      {canWrite && (
        <td>
          {!locked && <button className="kn-btn-sm" disabled={!docScore || !implScore || save.isPending} onClick={() => save.mutate()}>{t("common.save")}</button>}
          {!locked && save.isSuccess && <small role="status" className="kn-block kn-ok-text kn-mt-1">{t("common.saved")}</small>}
          {!locked && save.error && <small role="alert" className="kn-block kn-danger-text kn-mt-1">{save.error.message}</small>}
          <GapRiskButton item={item} assessment={assessment} />
        </td>
      )}
    </tr>
  );
}

export function Maturity() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const canWrite = user?.role === "admin" || user?.role === "grc_lead";
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [frameworkId, setFrameworkId] = useState("");
  const [name, setName] = useState("");
  const [asOfDate, setAsOfDate] = useState(new Date().toISOString().slice(0, 10));
  const frameworks = useQuery({ queryKey: ["frameworks"], queryFn: () => request<Framework[]>("/api/frameworks") });
  const assessments = useQuery({ queryKey: ["maturity-assessments"], queryFn: () => request<Assessment[]>("/api/maturity/assessments") });
  useEffect(() => {
    if (selectedId === null && assessments.data?.length) setSelectedId(assessments.data[0].id);
  }, [assessments.data, selectedId]);
  const selected = assessments.data?.find((assessment) => assessment.id === selectedId);
  const summary = useQuery({
    queryKey: ["maturity-summary", selectedId],
    queryFn: () => request<Summary>(`/api/maturity/assessments/${selectedId}/summary`),
    enabled: selectedId !== null,
  });
  const create = useMutation({
    mutationFn: () => request<Assessment>("/api/maturity/assessments", {
      method: "POST",
      body: JSON.stringify({ framework_id: Number(frameworkId), name, as_of_date: asOfDate }),
    }),
    onSuccess: async (assessment) => {
      setName("");
      setSelectedId(assessment.id);
      await queryClient.invalidateQueries({ queryKey: ["maturity-assessments"] });
    },
  });
  const finalize = useMutation({
    mutationFn: () => request<Assessment>(`/api/maturity/assessments/${selectedId}/finalize`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["maturity-assessments"] }),
  });
  // 一轮是 ceil(末级项 / 20) 次串行模型调用，所以只投递、不等结果（worker.py）。
  // AI 只补没打过分的项；人工打过的一律跳过，跳过几条记在任务的 summary 里。
  const suggest = useMutation({
    mutationFn: () => request<SuggestQueued>(`/api/maturity/assessments/${selectedId}/suggest`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["maturity-summary", selectedId] }),
  });
  const groupedItems = useMemo(() => {
    const result = new Map<number, MaturityItem[]>();
    for (const item of summary.data?.items ?? []) result.set(item.group_id, [...(result.get(item.group_id) ?? []), item]);
    return result;
  }, [summary.data]);
  // `data?.overall.total_items` 的可选链只保到 data——data 只要是个真值
  // （比如一个数组），`.overall` 就是 undefined，再取 `.total_items` 整页就塌。
  const completion = summary.data?.overall?.total_items
    ? Math.round((summary.data.overall.scored_items / summary.data.overall.total_items) * 100)
    : 0;

  const submitCreate = (event: FormEvent) => {
    event.preventDefault();
    create.mutate();
  };

  return (
    <section>
      <PageHeader
        title={t("maturity.title")}
        subtitle={t("maturity.subtitle")}
        actions={
          selected?.status === "draft" && canWrite ? (
            <>
            <button disabled={suggest.isPending} onClick={() => suggest.mutate()}>
              {suggest.isPending ? t("maturity.suggesting") : t("maturity.suggest")}
            </button>
            {/* 定稿不可撤回（后端没有反向操作），定稿后分数再也改不了 */}
            <button className="kn-btn-primary" disabled={finalize.isPending} onClick={() => { if (window.confirm(t("maturity.finalizeConfirm"))) finalize.mutate(); }}>{t("maturity.finalize")}</button>
            </>
          ) : undefined
        }
      />

      {suggest.error && <p role="alert" className="kn-danger-text kn-card">{suggest.error.message}</p>}
      {finalize.error && <p role="alert" className="kn-danger-text kn-card">{finalize.error.message}</p>}
      {suggest.data && (
        <p className="kn-card kn-mb-5">
          {t("maturity.suggestQueued", { id: suggest.data.job_id })}
        </p>
      )}

      {canWrite && (
        <form className="kn-card kn-form-grid-4" onSubmit={submitCreate}>
          <label>{t("maturity.framework")}<select required value={frameworkId} onChange={(event) => setFrameworkId(event.target.value)}><option value="">{t("maturity.chooseFramework")}</option>{frameworks.data?.map((framework) => <option key={framework.id} value={framework.id}>{framework.name_en || framework.name_zh} · {framework.version}</option>)}</select></label>
          <label>{t("maturity.assessmentName")}<input required value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>{t("maturity.asOfDate")}<input required type="date" value={asOfDate} onChange={(event) => setAsOfDate(event.target.value)} /></label>
          <button type="submit" disabled={create.isPending}>{t("maturity.create")}</button>
          <div className="kn-col-span-all">
            <PrerequisiteNotice
              items={[{ satisfied: (frameworks.data?.length ?? 0) > 0, label: t("maturity.prereqFrameworks"), to: "/frameworks" }]}
            />
          </div>
          {create.error && <p role="alert" className="kn-col-span-all kn-danger-text kn-m-0">{create.error.message}</p>}
        </form>
      )}

      <div className="kn-card kn-mb-5">
        <label className="kn-max-w-md">{t("maturity.assessment")}<select value={selectedId ?? ""} onChange={(event) => setSelectedId(Number(event.target.value))}><option value="">{t("maturity.chooseAssessment")}</option>{assessments.data?.map((assessment) => <option key={assessment.id} value={assessment.id}>{assessment.name} · {assessment.as_of_date} · {t(`maturity.${assessment.status}`)}</option>)}</select></label>
      </div>

      {(assessments.isPending || (selectedId !== null && summary.isPending)) && <LoadingState />}
      {(assessments.error || summary.error) && <p role="alert" className="kn-danger-text">{(assessments.error ?? summary.error)?.message}</p>}
      {!assessments.isPending && assessments.data?.length === 0 && <Card><EmptyState title={t("maturity.empty")} /></Card>}

      {selected && summary.data && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 14, marginBottom: 20 }}>
            {[[t("maturity.docAverage"), formatScore(summary.data.overall.doc_average)], [t("maturity.implAverage"), formatScore(summary.data.overall.impl_average)], [t("maturity.completion"), `${completion}%`], [t("maturity.status"), t(`maturity.${selected.status}`)]].map(([label, value]) => <div className="kn-card" key={label}><small className="kn-secondary">{label}</small><div style={{ fontSize: "1.75rem", fontWeight: 700, marginTop: 8 }}>{value}</div></div>)}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "minmax(300px, 0.9fr) minmax(360px, 1.1fr)", gap: 20, marginBottom: 20 }}>
            <div className="kn-card"><h3 className="kn-mt-0">{t("maturity.radarTitle")}</h3><div style={{ display: "flex", gap: 18, fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: 8 }}><span><i style={{ display: "inline-block", width: 16, borderTop: "2px solid var(--accent-blue)", marginRight: 6 }} />{t("maturity.docScore")}</span><span><i style={{ display: "inline-block", width: 16, borderTop: "2px solid var(--accent-emerald)", marginRight: 6 }} />{t("maturity.implScore")}</span></div><RadarChart groups={summary.data.groups} /></div>
            <Card title={t("maturity.groupSummary")}><DataTable className="kn-m-0" head={<><tr><th>{t("maturity.domain")}</th><th>{t("maturity.docScore")}</th><th>{t("maturity.implScore")}</th><th>{t("maturity.completion")}</th></tr></>}>{summary.data.groups.map((group) => <tr key={group.framework_item_id}><td><code>{group.code}</code><div className="kn-mt-1">{group.title}</div></td><td><span className="kn-score-pill" style={scoreTone(group.doc_average)}>{formatScore(group.doc_average)}</span></td><td><span className="kn-score-pill" style={scoreTone(group.impl_average)}>{formatScore(group.impl_average)}</span></td><td>{group.scored_items} / {group.total_items}</td></tr>)}</DataTable></Card>
          </div>

          <div className="kn-card">
            <h3 className="kn-mt-0">{t("maturity.detail")}</h3>
            <p className="kn-secondary kn-text-xs">{t("maturity.scaleHint")}</p>
            {summary.data.groups.map((group) => (
              <div key={group.framework_item_id} style={{ marginTop: 22 }}>
                <h4 style={{ marginBottom: 10 }}>{group.code} · {group.title}</h4>
                <DataTable className="kn-m-0" head={<><tr><th>{t("maturity.item")}</th><th>{t("maturity.docScore")}</th><th>{t("maturity.implScore")}</th><th>{t("maturity.rationale")}</th>{canWrite && <th>{t("maturity.action")}</th>}</tr></>}>{groupedItems.get(group.framework_item_id)?.map((item) => <ScoreEditor key={item.framework_item_id} item={item} assessment={selected} canWrite={canWrite} />)}</DataTable>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
