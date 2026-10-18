// 活动报告与打印视图。闭期结论来自关闭时的快照；当前跟进单独一节，不覆盖闭期数字。
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { Card, DataTable, EmptyState, LoadingState, PageHeader, StatCard } from "../../components/ui";
import { api } from "./api";
import { ErrorNotice, RcsaGate, StatusBadge } from "./shared";

function Counts({ title, counts }: { title: string; counts: Record<string, number> }) {
  const { t } = useTranslation();
  const entries = Object.entries(counts);
  return (
    <Card title={title}>
      {entries.length === 0 ? <p className="kn-secondary kn-m-0">{t("rcsa.report.none")}</p> : (
        <ul className="kn-stack-1">{entries.map(([key, value]) => <li key={key}>{key}: <strong>{value}</strong></li>)}</ul>
      )}
    </Card>
  );
}

export function Report() {
  const { t } = useTranslation();
  const id = Number(useParams().id);
  const report = useQuery({ queryKey: ["rcsa", "report", id], queryFn: () => api.report(id) });
  if (report.isPending) return <section className="kn-page"><LoadingState /></section>;
  if (report.error) return <section className="kn-page"><ErrorNotice error={report.error} /></section>;
  const r = report.data;
  return (
    <section className="kn-page rcsa-report">
      <RcsaGate>
        <PageHeader
          title={t("rcsa.report.title", { name: r.campaign.name })}
          badge={r.watermark ? <span className="rcsa-watermark" role="note">{t(`rcsa.report.watermark.${r.watermark}`)}</span> : <StatusBadge status={r.campaign.status} />}
          subtitle={t("rcsa.report.basis", {
            start: r.campaign.period_start, end: r.campaign.period_end,
            profile: r.campaign.profile.key ? `${r.campaign.profile.key} v${r.campaign.profile.revision}` : "—",
            horizon: r.campaign.profile.horizon ?? "—",
          })}
          actions={
            <div className="kn-row kn-row-gap rcsa-no-print">
              <Link className="kn-btn kn-btn-secondary" to={`/rcsa/campaigns/${id}`}>{t("rcsa.report.back")}</Link>
              <button type="button" className="kn-btn kn-btn-primary" onClick={() => window.print()}>{t("rcsa.report.print")}</button>
            </div>
          }
        />
        <p className="kn-secondary kn-text-xs">
          {r.source === "closed_snapshot" ? t("rcsa.report.fromSnapshot", { date: r.campaign.closed_at?.slice(0, 10) }) : t("rcsa.report.live")}
          {" "}{t("rcsa.report.disclaimer")}{" "}{t(`rcsa.report.ai.${r.ai_assistance}`)}
        </p>
        <div className="rcsa-figures">
          <StatCard label={t("rcsa.report.inScope")} value={r.scope.total} />
          <StatCard label={t("rcsa.report.approved")} value={r.scope.approved} tone="success" />
          <StatCard label={t("rcsa.report.excluded")} value={r.scope.excluded} />
          <StatCard label={t("rcsa.report.unfinished")} value={r.scope.submitted + r.scope.in_progress} tone={r.scope.submitted + r.scope.in_progress ? "warning" : undefined} />
          <StatCard label={t("rcsa.report.openActions")} value={r.actions.open} hint={t("rcsa.report.overdueHint", { count: r.actions.overdue })} tone={r.actions.overdue ? "danger" : undefined} />
          <StatCard label={t("rcsa.report.highestResidual")} value={r.highest_residual_band ?? "—"} />
        </div>
        <div className="kn-grid-2">
          <Card title={t("rcsa.report.exclusions")}>
            {r.exclusions.length === 0 ? <p className="kn-secondary kn-m-0">{t("rcsa.report.none")}</p> : (
              <ul className="kn-stack-1">{r.exclusions.map((e) => <li key={e.control_code}><strong>{e.control_code}</strong> — {e.reason}</li>)}</ul>
            )}
          </Card>
          <Card title={t("rcsa.report.notAssessed")}>
            {r.not_assessed.length === 0 ? <p className="kn-secondary kn-m-0">{t("rcsa.report.none")}</p> : (
              <ul className="kn-stack-1">{r.not_assessed.map((n) => (
                <li key={n.control_code}><strong>{n.control_code}</strong> ({n.ratings.map((x) => t(`rcsa.assessment.${x}`)).join(", ")}) — {n.reason}
                  {n.follow_up_due ? <span className="kn-secondary"> · {t("rcsa.dueOn", { date: n.follow_up_due })}</span> : null}</li>
              ))}</ul>
            )}
          </Card>
          <Counts title={t("rcsa.report.independentTests")} counts={Object.fromEntries(Object.entries(r.independent_tests).map(([k, v]) => [t(`rcsa.testStatus.${k}`), v]))} />
          <Counts title={t("rcsa.report.dispositions")} counts={Object.fromEntries(Object.entries(r.dispositions).map(([k, v]) => [k === "none" ? t("rcsa.risks.noDisposition") : t(`rcsa.risks.strategies.${k}`), v]))} />
          <Counts title={t("rcsa.report.inherentBands")} counts={r.scenario_bands.inherent} />
          <Counts title={t("rcsa.report.residualBands")} counts={r.scenario_bands.residual} />
          <Card title={t("rcsa.report.acceptances")}>
            {r.acceptances.length === 0 ? <p className="kn-secondary kn-m-0">{t("rcsa.report.none")}</p> : (
              <ul className="kn-stack-1">{r.acceptances.map((a) => (
                <li key={`${a.control_code}-${a.scenario_key}`}>{a.control_code} / {a.scenario_key} — {t("rcsa.risks.until", { date: a.accept_until })}
                  {a.expired ? <span className="rcsa-overdue"> {t("rcsa.status.expired")}</span> : null}</li>
              ))}</ul>
            )}
          </Card>
          <Card title={t("rcsa.report.limitations")}>
            {r.limitations.length === 0 && !r.acknowledgements?.overdue && !r.acknowledgements?.evidence
              ? <p className="kn-secondary kn-m-0">{t("rcsa.report.none")}</p> : (
                <ul className="kn-stack-1">
                  {r.limitations.map((l) => <li key={l.control_code}><strong>{l.control_code}</strong> — {l.text}</li>)}
                  {r.acknowledgements?.overdue ? <li>{t("rcsa.transitions.overdueAck")}: {r.acknowledgements.overdue}</li> : null}
                  {r.acknowledgements?.evidence ? <li>{t("rcsa.transitions.evidenceAck")}: {r.acknowledgements.evidence}</li> : null}
                </ul>
              )}
          </Card>
        </div>
        <Card title={t("rcsa.report.items")}>
          {r.items.length === 0 ? <EmptyState compact /> : (
            <DataTable className="kn-m-0" head={<tr><th>{t("rcsa.scope.control")}</th><th>{t("rcsa.campaigns.status")}</th><th>{t("rcsa.assessment.design")}</th><th>{t("rcsa.assessment.operating")}</th><th>{t("rcsa.assessment.independentTest")}</th><th>{t("rcsa.report.scenarios")}</th></tr>}>
              {r.items.map((item) => (
                <tr key={item.item_id}>
                  <td><strong>{item.control_code}</strong> {item.control_title}</td>
                  <td><StatusBadge status={item.status} /></td>
                  <td>{item.design ? t(`rcsa.effectiveness.${item.design}`) : "—"}</td>
                  <td>{item.operating ? t(`rcsa.effectiveness.${item.operating}`) : "—"}</td>
                  <td>{item.independent_test_status ? t(`rcsa.testStatus.${item.independent_test_status}`) : "—"}</td>
                  <td>{item.scenarios.map((s) => (
                    <div key={s.scenario_key} className="kn-text-xs">{s.event}: {s.inherent_band} → {s.residual_band}
                      {s.disposition ? ` · ${t(`rcsa.risks.strategies.${s.disposition}`)}` : ""}</div>
                  ))}</td>
                </tr>
              ))}
            </DataTable>
          )}
        </Card>
        {r.comparison && (
          <Card title={t("rcsa.report.comparison", { name: r.comparison.previous.name })}>
            <p className="kn-secondary kn-text-xs">
              {r.comparison.reason ? t(`rcsa.report.comparisonReason.${r.comparison.reason}`) : t("rcsa.report.comparisonRule")}
            </p>
            {r.comparison.scenarios.length === 0 ? <p className="kn-secondary kn-m-0">{t("rcsa.report.none")}</p> : (
              <DataTable className="kn-m-0" head={<tr><th>{t("rcsa.scope.control")}</th><th>{t("rcsa.risks.key")}</th><th>{t("rcsa.report.previous")}</th><th>{t("rcsa.report.current")}</th><th>{t("rcsa.campaigns.status")}</th></tr>}>
                {r.comparison.scenarios.map((row) => (
                  <tr key={`${row.control_code}-${row.scenario_key}`}>
                    <td>{row.control_code}</td>
                    <td>{row.scenario_key}</td>
                    <td>{row.previous_residual_band ? `${row.previous_residual_band} (${row.previous_residual_score})` : "—"}</td>
                    <td>{row.current_residual_band ? `${row.current_residual_band} (${row.current_residual_score})` : "—"}</td>
                    <td>{t(`rcsa.report.comparisonStatus.${row.status}`)}</td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Card>
        )}
        <Card title={t("rcsa.report.currentFollowUp")}>
          <p className="kn-secondary kn-text-xs">{t("rcsa.report.currentHint")}</p>
          {r.current_follow_up.actions.length === 0 ? <p className="kn-secondary kn-m-0">{t("rcsa.report.none")}</p> : (
            <ul className="kn-stack-1">{r.current_follow_up.actions.map((a) => (
              <li key={a.id}>{a.control_code} — {a.title} <StatusBadge status={a.status} />{" "}
                <span className={a.overdue ? "rcsa-overdue" : "kn-secondary"}>{a.overdue ? t("rcsa.overdueSince", { date: a.due_at }) : t("rcsa.dueOn", { date: a.due_at })}</span></li>
            ))}</ul>
          )}
        </Card>
      </RcsaGate>
    </section>
  );
}
