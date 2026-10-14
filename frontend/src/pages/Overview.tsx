import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { request } from "../api";
import { reviewState } from "../reviewDue";

interface Usage {
  month_to_date_cost: number;
  budget: number | null;
  /** 本月有多少次调用没算进成本（模型不在价格表里）。>0 时上面的花费是下限 */
  uncosted_calls?: number;
  by_task: { task_key: string; cost: number; calls: number }[];
}

interface EvidenceStats {
  expired: number;
}

interface ReviewDocument {
  id: number;
  status: string;
  review_due_date: string | null;
}

function StatusBadge({ loaded, failed, attention }: { loaded: boolean; failed: boolean; attention: boolean }) {
  const { t } = useTranslation();
  if (failed) return <span role="alert" className="kn-badge kn-badge-amber">{t("overview.unavailable")}</span>;
  if (!loaded) return <span className="kn-badge">—</span>;
  return (
    <span className={`kn-badge ${attention ? "kn-badge-ruby" : "kn-badge-emerald"}`}>
      {attention ? t("overview.needsAttention") : t("overview.upToDate")}
    </span>
  );
}

export function Overview() {
  const { t } = useTranslation();
  const { data } = useQuery({
    queryKey: ["usage"],
    queryFn: () => request<Usage>("/api/settings/usage"),
  });
  const evidenceStats = useQuery({
    queryKey: ["evidence-stats"],
    queryFn: () => request<EvidenceStats>("/api/evidence/stats"),
  });
  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: () => request<ReviewDocument[]>("/api/documents"),
  });

  const overdue = (documents.data ?? []).filter((doc) => reviewState(doc) === "overdue").length;
  const soon = (documents.data ?? []).filter((doc) => reviewState(doc) === "soon").length;
  // 已被新版替换的旧版不再受控；算进来，每换一次版这个数就多一。
  const governed = documents.data?.filter((doc) => doc.status !== "superseded").length;

  const cost = data?.month_to_date_cost ?? 0;
  const budget = data?.budget;
  const budgetRatio = budget && budget > 0 ? Math.min(Math.round((cost / budget) * 100), 100) : null;
  const totalCalls = data?.by_task.reduce((sum, r) => sum + r.calls, 0) ?? 0;
  const maxTaskCost = Math.max(...(data?.by_task.map((r) => r.cost) ?? [1]), 0.0001);

  const proposalStats = useQuery({
    queryKey: ["proposal-stats"],
    queryFn: () => request<{ pending: number }>("/api/proposals/stats"),
  });
  const pendingCount = proposalStats.data?.pending ?? 0;

  return (
    <div className="kn-page kn-page-overview" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Executive Hero Cockpit Banner */}
      <div className="kn-cockpit-hero">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
              <span className="kn-pulse-dot" />
              <span className="kn-text-2xs kn-badge kn-badge-emerald">
                {t("overview.compliancePosture")}
              </span>
            </div>
            <h2 style={{ margin: 0, fontSize: "1.75rem", letterSpacing: "-0.03em", fontWeight: 700 }}>
              {t("overview.heroTitle")}
            </h2>
            <p style={{ margin: "6px 0 0", color: "var(--text-secondary)", fontSize: "0.875rem" }}>
              {t("overview.heroSubtitle")}
            </p>
          </div>
          {pendingCount > 0 && (
            <Link to="/review" className="kn-btn-primary kn-btn-sm" style={{ textDecoration: "none" }}>
              <span>{t("overview.pendingReviewQuick")} ({pendingCount})</span>
              <span>→</span>
            </Link>
          )}
        </div>

        <div className="kn-cockpit-metrics-strip">
          <div className="kn-cockpit-metric">
            <span className="kn-eyebrow">
              {t("overview.activeDocuments")}
            </span>
            <div className="kn-cockpit-metric-num">{governed ?? "—"}</div>
          </div>
          <div className="kn-cockpit-metric">
            <span className="kn-eyebrow">
              {t("overview.monthCost")}
            </span>
            {/* 没拿到数据时是「—」而不是 $0.00：后者读起来是「本月没花钱」 */}
            <div className="kn-cockpit-metric-num">{data ? `$${data.month_to_date_cost.toFixed(2)}` : "—"}</div>
          </div>
          <div className="kn-cockpit-metric">
            <span className="kn-eyebrow">
              {t("overview.totalCalls")}
            </span>
            <div className="kn-cockpit-metric-num">{data ? totalCalls.toLocaleString() : "—"}</div>
          </div>
          <div className="kn-cockpit-metric">
            <span className="kn-eyebrow">
              {t("overview.reviewAlerts")}
            </span>
            <div className="kn-cockpit-metric-num" style={{ color: overdue > 0 ? "var(--accent-ruby)" : soon > 0 ? "var(--accent-amber)" : undefined }}>
              {documents.data ? overdue + soon : "—"}
            </div>
          </div>
        </div>
      </div>

      <div className="kn-bento-grid">
        <div className="kn-card kn-stat-card">
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span className="kn-stat-label">{t("overview.monthCost")}</span>
              {budgetRatio !== null && (
                <span className={`kn-badge ${budgetRatio > 90 ? "kn-badge-ruby" : budgetRatio > 70 ? "kn-badge-amber" : "kn-badge-emerald"}`}>
                  {budgetRatio}%
                </span>
              )}
            </div>
            <div className="kn-stat-number kn-primary-text">
              ${data ? data.month_to_date_cost.toFixed(2) : "—"}
            </div>
            <p style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", margin: "4px 0 8px" }}>
              {t("overview.monthCost")}: ${data?.month_to_date_cost.toFixed(2) ?? "—"}
              {data?.budget != null && ` / $${data.budget.toFixed(2)}`}
            </p>
            {/* 未计价的调用必须说出来。不说的话，一个用未知模型的部署会永远
                显示 $0.00，预算闸门静默放行，而这张卡看起来一切正常。 */}
            {(data?.uncosted_calls ?? 0) > 0 && (
              <p role="note" className="kn-prereq-notice kn-mb-2">
                {t("overview.uncostedCalls", { count: data!.uncosted_calls })}
              </p>
            )}
          </div>
          {budget != null && budgetRatio !== null && (
            <div className="kn-mt-2">
              <div className="kn-progress-bar" style={{ height: 6, borderRadius: 9999 }}>
                <div
                  className="kn-progress-fill"
                  style={{
                    width: `${budgetRatio}%`,
                    background: budgetRatio > 90 ? "var(--accent-ruby)" : budgetRatio > 70 ? "var(--accent-amber)" : "linear-gradient(90deg, #059669, #10b981)",
                    boxShadow: budgetRatio > 90 ? "0 0 10px rgba(255, 69, 58, 0.4)" : budgetRatio > 70 ? "0 0 10px rgba(255, 159, 10, 0.4)" : "0 0 10px rgba(16, 185, 129, 0.4)",
                  }}
                />
              </div>
            </div>
          )}
        </div>

        <div className="kn-card kn-stat-card">
          <div>
            <span className="kn-stat-label">{t("overview.expiredEvidence")}</span>
            <div className="kn-stat-number" style={{ color: evidenceStats.data?.expired ? "var(--accent-ruby)" : undefined }}>
              {evidenceStats.data?.expired ?? "—"}
            </div>
            <div className="kn-stat-sub">{t("overview.expiredEvidenceHint")}</div>
          </div>
          {/* 没拿到数据时不能显示「当前有效」：加载失败读起来成了「一切正常」 */}
          <StatusBadge loaded={!!evidenceStats.data} failed={!!evidenceStats.error} attention={!!evidenceStats.data?.expired} />
        </div>

        <div className="kn-card kn-stat-card" data-card="review-due">
          <div>
            <span className="kn-stat-label">{t("documents.reviewDue")}</span>
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 12 }}>
              <Link
                to="/documents?review=overdue"
                style={{ color: overdue ? "var(--accent-ruby)" : "var(--text-primary)", fontWeight: 600, textDecoration: "none" }}
              >
                {t("documents.reviewOverdue", { count: overdue })}
              </Link>
              <Link
                to="/documents?review=soon"
                style={{ color: soon ? "var(--accent-amber)" : "var(--text-primary)", fontWeight: 600, textDecoration: "none" }}
              >
                {t("documents.reviewSoon", { count: soon })}
              </Link>
            </div>
          </div>
          <StatusBadge loaded={!!documents.data} failed={!!documents.error} attention={overdue + soon > 0} />
        </div>

        <div className="kn-card kn-stat-card">
          <div>
            <span className="kn-stat-label">{t("overview.calls")}</span>
            <div className="kn-stat-number kn-primary-text">
              {totalCalls.toLocaleString()}
            </div>
            <div className="kn-stat-sub">
              {t("overview.taskTypes", { count: data?.by_task.length ?? 0 })}
            </div>
          </div>
        </div>
      </div>

      <div className="kn-card" style={{ padding: 0 }}>
        <div style={{ padding: "18px 24px", borderBottom: "1px solid var(--stage-border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h3 style={{ margin: 0, fontSize: "1.0625rem" }}>{t("overview.calls")}</h3>
          <span className="kn-badge kn-badge-emerald">{data?.by_task.length ?? 0}</span>
        </div>
        <div className="kn-table-container" style={{ margin: 0, border: "none", borderRadius: 0, boxShadow: "none" }}>
          <table>
            <thead>
              <tr>
                <th>{t("overview.task")}</th>
                <th>{t("overview.calls")}</th>
                <th>{t("overview.cost")}</th>
              </tr>
            </thead>
            <tbody>
              {data?.by_task.map((row) => (
                <tr key={row.task_key}>
                  <td className="kn-medium">
                    <code style={{ fontSize: "0.8125rem", color: "var(--accent-emerald)", background: "rgba(16, 185, 129, 0.08)", padding: "2px 6px", borderRadius: 4, border: "1px solid rgba(16, 185, 129, 0.2)" }}>
                      {t(`taskNames.${row.task_key}`, { defaultValue: row.task_key })}
                    </code>
                  </td>
                  <td>
                    <div className="kn-row">
                      <span style={{ minWidth: 40, fontVariantNumeric: "tabular-nums" }}>{row.calls.toLocaleString()}</span>
                    </div>
                  </td>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                      <span style={{ minWidth: 68, fontVariantNumeric: "tabular-nums", fontWeight: 600 }}>
                        ${row.cost.toFixed(4)}
                      </span>
                      <div style={{ flex: 1, maxWidth: 120 }}>
                        <div className="kn-progress-bar" style={{ height: 4, borderRadius: 9999 }}>
                          <div
                            className="kn-progress-fill"
                            style={{
                              width: `${Math.max((row.cost / maxTaskCost) * 100, 4)}%`,
                              background: "linear-gradient(90deg, #059669, #10b981)",
                            }}
                          />
                        </div>
                      </div>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
