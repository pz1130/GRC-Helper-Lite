// 导航模型：分组、待办角标、后端心跳、面包屑。
// 从 Layout 抽出来，是因为不同外壳（侧栏 / 顶栏 / 图标轨）要渲染同一份导航，
// 数据与「心跳必须真的去问后端」这类约束只该写一次。
import { useMemo, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";

import { request } from "../api";

interface ProposalStats {
  pending: number;
  by_kind: Record<string, number>;
}

interface HealthStatus {
  status: string;
  database: string;
  version: string;
}

export interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  badge?: number | null;
}

export interface NavGroup {
  id: string;
  title: string;
  items: NavItem[];
}

export function useNavModel() {
  const { t } = useTranslation();
  const location = useLocation();
  // 实时拉提案统计，给确认队列的角标用。
  const proposalStats = useQuery({
    queryKey: ["proposal-stats"],
    queryFn: () => request<ProposalStats>("/api/proposals/stats"),
    refetchInterval: 20000,
    staleTime: 10000,
  });

  const pendingCount = proposalStats.data?.pending ?? 0;

  // RCSA 入口只在部署打开了模块时出现。审计范围账号拿到 403，同样不显示。
  const rcsaStatus = useQuery({
    queryKey: ["rcsa", "status"],
    queryFn: () => request<{ enabled: boolean }>("/api/rcsa/status"),
    staleTime: 60_000,
    retry: false,
  });
  const rcsaEnabled = rcsaStatus.data?.enabled === true;

  // 顶部心跳真的去问后端。原先这里是个写死的绿点：库连不上时它照样显示
  // "系统运行正常"，等于对着审计人员撒谎，而这正是最不该骗人的一个位置。
  //
  // retry: false —— 后端降级时返回 503，默认的三次退避重试会把红灯拖到十几秒
  // 之后才亮；这个指示器的全部价值就在于立刻显形。
  const healthQuery = useQuery({
    queryKey: ["system-health"],
    queryFn: () => request<HealthStatus>("/api/health"),
    refetchInterval: 30000,
    retry: false,
  });
  // 请求失败（503、网络不通）和 status !== "ok" 是同一件事：都不能显示正常。
  const healthOk = healthQuery.isSuccess && healthQuery.data.status === "ok";
  const healthLabel = healthQuery.isPending
    ? t("topbar.systemChecking")
    : healthOk
      ? t("topbar.systemHealthy")
      : t("topbar.systemDegraded");

  // 导航分组：四个业务域。
  const navGroups: NavGroup[] = useMemo(
    () => [
      {
        id: "governance",
        title: t("nav.groups.governance"),
        items: [
          {
            to: "/",
            label: t("nav.overview"),
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="7" height="7" rx="1.5" />
                <rect x="14" y="3" width="7" height="7" rx="1.5" />
                <rect x="14" y="14" width="7" height="7" rx="1.5" />
                <rect x="3" y="14" width="7" height="7" rx="1.5" />
              </svg>
            ),
          },
          {
            to: "/frameworks",
            label: t("nav.frameworks"),
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="12 2 2 7 12 12 22 7 12 2" />
                <polyline points="2 17 12 22 22 17" />
                <polyline points="2 12 12 17 22 12" />
              </svg>
            ),
          },
          {
            to: "/controls",
            label: t("nav.controls"),
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                <circle cx="12" cy="11" r="3" />
              </svg>
            ),
          },
          {
            to: "/documents",
            label: t("nav.documents"),
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="16" y1="13" x2="8" y2="13" />
                <line x1="16" y1="17" x2="8" y2="17" />
              </svg>
            ),
          },
        ],
      },
      {
        id: "defense",
        title: t("nav.groups.defense"),
        items: [
          {
            to: "/review",
            label: t("nav.review"),
            badge: pendingCount > 0 ? pendingCount : null,
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 11l3 3L22 4" />
                <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
              </svg>
            ),
          },
          {
            to: "/audit",
            label: t("nav.audit"),
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" />
                <rect x="9" y="3" width="6" height="4" rx="1" />
                <path d="M9 12h6M9 16h4" />
              </svg>
            ),
          },
          ...(rcsaEnabled
            ? [{
                to: "/rcsa",
                label: t("nav.rcsa"),
                icon: (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M9 11l3 3L22 4" />
                    <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
                  </svg>
                ),
              }]
            : []),
          {
            to: "/evidence",
            label: t("nav.evidence"),
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <path d="M14 2v6h6M8 13h8M8 17h6" />
              </svg>
            ),
          },
        ],
      },
      {
        id: "intelligence",
        title: t("nav.groups.intelligence"),
        items: [
          {
            to: "/graph",
            label: t("nav.graph"),
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="5" cy="6" r="2.5" />
                <circle cx="19" cy="9" r="2.5" />
                <circle cx="9" cy="18" r="2.5" />
                <path d="M7.2 7.1 16.6 8.5M17.6 11.2 10.7 16.2M7.3 8.4 8.4 15.6" />
              </svg>
            ),
          },
          {
            to: "/maturity",
            label: t("nav.maturity"),
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 19V9M10 19V5M16 19v-7M22 19V3" />
                <path d="M2 19h22" />
              </svg>
            ),
          },
          {
            to: "/risks",
            label: t("nav.risks"),
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3 2 21h20L12 3z" />
                <path d="M12 9v5M12 18h.01" />
              </svg>
            ),
          },
          {
            to: "/tech-assets",
            label: t("nav.techAssets"),
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="4" width="18" height="16" rx="2" />
                <path d="M7 8h10M7 12h4M7 16h7" />
              </svg>
            ),
          },
        ],
      },
      {
        id: "system",
        title: t("nav.groups.system"),
        items: [
          {
            to: "/search",
            label: t("nav.search"),
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
            ),
          },
          {
            to: "/settings",
            label: t("nav.settings"),
            icon: (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            ),
          },
        ],
      },
    ],
    [t, pendingCount, rcsaEnabled],
  );

  // 面包屑按当前路由推导，不另存一份状态。
  const breadcrumb = useMemo(() => {
    const path = location.pathname;
    if (path === "/") {
      return { section: t("nav.groups.governance"), page: t("nav.overview") };
    }
    for (const group of navGroups) {
      for (const item of group.items) {
        if (item.to !== "/" && (path === item.to || path.startsWith(item.to + "/"))) {
          return { section: group.title, page: item.label };
        }
      }
    }
    return { section: t("app.name"), page: "" };
  }, [location.pathname, navGroups, t]);

  const healthState: "checking" | "ok" | "down" = healthQuery.isPending ? "checking" : healthOk ? "ok" : "down";
  return { navGroups, pendingCount, healthOk, healthLabel, healthState, breadcrumb };
}
