import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Card, DataTable, StatCard } from "../components/ui";
import { Link } from "react-router-dom";
import { lazy, Suspense, useState } from "react";

import { wantsHero3d } from "../three/support";

import { request } from "../api";
import { reviewState } from "../reviewDue";

// three.js 很大，hero 的 3D 版按需加载；首屏先出 CSS 版，加载完再替换。
const HeroV2 = lazy(() => import("../three/HeroV2"));

type HeroConcept = "a" | "b" | "c";
/** hero 概念：开发对比用 `?hero=a|b|c`；不带参数时用默认概念。 */
function heroConcept(): HeroConcept {
  const q = new URLSearchParams(window.location.search).get("hero");
  return q === "a" || q === "b" || q === "c" ? q : DEFAULT_HERO;
}
// 默认用风险地形：唯一由真实数据驱动的概念，且明暗主题下都最耐看。
const DEFAULT_HERO: HeroConcept = "c";

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
  // 只在首次渲染时判定一次：中途切换会让 hero 闪一下，没必要。
  const [hero3d] = useState(wantsHero3d);
  const [concept] = useState(heroConcept);
  // 风险地形（概念 c）用真实的风险登记数据塑形；其它概念不需要，避免多一个请求。
  const risksQ = useQuery({
    queryKey: ["risks"],
    queryFn: () => request<{ likelihood: number; impact: number; inherent_score: number; status: string }[]>("/api/risks"),
    enabled: hero3d && concept === "c",
  });
  const riskPoints = (risksQ.data ?? []).map((r) => ({ likelihood: r.likelihood, impact: r.impact, score: r.inherent_score, open: r.status !== "closed" && r.status !== "accepted" }));

  const cssHero = (
    <div className="kn-hero-stack" aria-hidden="true">
      <span className="kn-hero-layer kn-hero-layer--1" />
      <span className="kn-hero-layer kn-hero-layer--2" />
      <span className="kn-hero-layer kn-hero-layer--3" />
    </div>
  );

  return (
    <div className="kn-page kn-page-overview" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Executive Hero Cockpit Banner */}
      <div className={`kn-cockpit-hero${hero3d ? " kn-cockpit-hero--v2" : ""}`}>
        {/* 装饰用的「治理分层」立体示意：有 WebGL 时是真 3D 玻璃板，否则退回纯 CSS 3D；读屏忽略。 */}
        {hero3d ? (
          <Suspense fallback={cssHero}>
            <HeroV2 concept={concept} risks={concept === "c" ? riskPoints : undefined} />
          </Suspense>
        ) : (
          cssHero
        )}
        <div className="kn-hero-top">
          <div className="kn-hero-copy">
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
              <span className="kn-pulse-dot" />
              <span className="kn-text-2xs kn-badge kn-badge-emerald">
                {t("overview.compliancePosture")}
              </span>
            </div>
            <h2 className="kn-hero-title">{t("overview.heroTitle")}</h2>
            <p className="kn-hero-subtitle">{t("overview.heroSubtitle")}</p>
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

        {/* 没拿到数据时不能显示「当前有效」：加载失败读起来成了「一切正常」 */}
        <StatCard
          label={t("overview.expiredEvidence")}
          value={evidenceStats.data?.expired ?? "—"}
          hint={t("overview.expiredEvidenceHint")}
          tone={evidenceStats.data?.expired ? "danger" : undefined}
          footer={<StatusBadge loaded={!!evidenceStats.data} failed={!!evidenceStats.error} attention={!!evidenceStats.data?.expired} />}
        />

        <div className="kn-card kn-stat-card" data-card="review-due">
          <div>
            <span className="kn-stat-label">{t("documents.reviewDue")}</span>
            <div className="kn-stat-links">
              <Link
                to="/documents?review=overdue"
                className={`kn-stat-link${overdue ? " kn-danger-text" : ""}`}
              >
                {t("documents.reviewOverdue", { count: overdue })}
              </Link>
              <Link
                to="/documents?review=soon"
                className={`kn-stat-link${soon ? " kn-warn" : ""}`}
              >
                {t("documents.reviewSoon", { count: soon })}
              </Link>
            </div>
          </div>
          <StatusBadge loaded={!!documents.data} failed={!!documents.error} attention={overdue + soon > 0} />
        </div>

        <StatCard
          label={t("overview.calls")}
          value={totalCalls.toLocaleString()}
          hint={t("overview.taskTypes", { count: data?.by_task.length ?? 0 })}
        />
      </div>

      <Card className="kn-card--flush">
        <div className="kn-card-section-head">
          <h3>{t("overview.calls")}</h3>
          <span className="kn-badge kn-badge-emerald">{data?.by_task.length ?? 0}</span>
        </div>
        <DataTable
          className="kn-table-flush"
          head={
            <tr>
              <th>{t("overview.task")}</th>
              <th>{t("overview.calls")}</th>
              <th>{t("overview.cost")}</th>
            </tr>
          }
        >
          {data?.by_task.map((row) => (
            <tr key={row.task_key}>
              <td className="kn-medium">
                <code className="kn-task-code">{t(`taskNames.${row.task_key}`, { defaultValue: row.task_key })}</code>
              </td>
              <td className="kn-tabular">{row.calls.toLocaleString()}</td>
              <td>
                <div className="kn-cost-row">
                  <span className="kn-cost-amount">${row.cost.toFixed(4)}</span>
                  <div className="kn-cost-bar">
                    <div className="kn-progress-bar">
                      {/* 宽度随数据变化，只能留在 style 里 */}
                      <div className="kn-progress-fill" style={{ width: `${Math.max((row.cost / maxTaskCost) * 100, 4)}%` }} />
                    </div>
                  </div>
                </div>
              </td>
            </tr>
          ))}
        </DataTable>
      </Card>
    </div>
  );
}
