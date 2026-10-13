import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { PageErrorBoundary } from "./ErrorBoundary";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";

import { useAuth } from "../auth";
import { request } from "../api";
import { useTheme } from "../theme";
import { BrandLogo } from "./BrandLogo";

interface ProposalStats {
  pending: number;
  by_kind: Record<string, number>;
}

interface HealthStatus {
  status: string;
  database: string;
  version: string;
}

export function Layout() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const location = useLocation();
  // 窄屏抽屉。宽屏下这个状态没有任何作用——CSS 那边 768px 以上侧栏常驻。
  const [drawerOpen, setDrawerOpen] = useState(false);
  const navigate = useNavigate();

  // 实时拉提案统计，给确认队列的角标用。
  const proposalStats = useQuery({
    queryKey: ["proposal-stats"],
    queryFn: () => request<ProposalStats>("/api/proposals/stats"),
    refetchInterval: 20000,
    staleTime: 10000,
  });

  const pendingCount = proposalStats.data?.pending ?? 0;

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

  // Global ⌘K / Ctrl+K shortcut listener
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        navigate("/search");
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [navigate]);

  // 导航分组：四个业务域。
  const navGroups = useMemo(
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
    [t, pendingCount],
  );

  // 跳转后收起抽屉，否则它盖着刚打开的页面
  useEffect(() => setDrawerOpen(false), [location.pathname]);

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

  return (
    <div style={{ display: "flex", minHeight: "100vh", position: "relative", width: "100%" }}>
      {/* ====================================================================
          Obsidian Studio Pro Sidebar
         ==================================================================== */}
      {/* 窄屏下点内容区任意处收起抽屉。没有这层遮罩，抽屉开着时
          背后的表格仍然可点，人会以为自己点空了。 */}
      {drawerOpen && <div className="kn-drawer-backdrop" onClick={() => setDrawerOpen(false)} />}
      {/* 布局样式全部在 .kn-sidebar 里，**不要写回内联**——内联样式优先级高于
          media query，写回去窄屏那套就再也生效不了。 */}
      <nav className={`kn-sidebar${drawerOpen ? " kn-sidebar--open" : ""}`}>
        {/* Brand Header */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20, padding: "0 6px" }}>
          <BrandLogo size={32} />
          <div>
            <h1
              style={{
                fontSize: "1.0625rem",
                margin: 0,
                fontWeight: 700,
                letterSpacing: "-0.02em",
                color: "var(--text-primary)",
                lineHeight: 1.2,
              }}
            >
              {t("app.name")}
            </h1>
            <div style={{ fontSize: "0.6875rem", color: "var(--text-tertiary)", fontWeight: 500 }}>
              {t("app.tagline")}
            </div>
          </div>
        </div>

        {/* Scrollable Navigation Area (Categorized Groups) */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 16,
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
            paddingRight: 2,
          }}
        >
          {navGroups.map((group) => (
            <div key={group.id} className="kn-nav-group">
              <div className="kn-nav-group-header">{group.title}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {group.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === "/"}
                    className={({ isActive }) => `kn-sidebar-nav-item ${isActive ? "active" : ""}`}
                  >
                    <span className="kn-sidebar-nav-item-icon">{item.icon}</span>
                    <span className="kn-sidebar-nav-item-label">{item.label}</span>
                    {"badge" in item && item.badge != null && (
                      <span className="kn-sidebar-nav-badge">{item.badge}</span>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </div>

        <hr style={{ margin: "14px 0 10px 0", borderColor: "var(--stage-border)", opacity: 0.6 }} />

        {/* User Identity Card */}
        <div className="kn-user-card-compact">
          <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flex: 1 }}>
            <div
              style={{
                width: 28,
                height: 28,
                borderRadius: "50%",
                background: "var(--avatar-bg)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: "0.75rem",
                fontWeight: 600,
                color: "var(--text-primary)",
                border: "1px solid var(--stage-border)",
                flexShrink: 0,
              }}
            >
              {user?.name?.[0]?.toUpperCase() ?? "U"}
            </div>
            <div style={{ minWidth: 0, flex: 1, overflow: "hidden" }}>
              <div
                style={{
                  fontSize: "0.8125rem",
                  fontWeight: 600,
                  color: "var(--text-primary)",
                  whiteSpace: "nowrap",
                  textOverflow: "ellipsis",
                  overflow: "hidden",
                  lineHeight: 1.25,
                }}
              >
                <NavLink
                  to="/account"
                  aria-label={t("app.account")}
                  title={t("app.account")}
                  style={{ color: "inherit", textDecoration: "none" }}
                >
                  {user?.name}
                </NavLink>
              </div>
              <div
                style={{
                  fontSize: "0.6875rem",
                  color: "var(--text-tertiary)",
                  whiteSpace: "nowrap",
                  textOverflow: "ellipsis",
                  overflow: "hidden",
                  lineHeight: 1.25,
                }}
              >
                {user?.role}
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={logout}
            className="kn-btn-logout-compact"
            title={t("app.logout")}
            aria-label={t("app.logout")}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
            <span>{t("app.logout")}</span>
          </button>
        </div>

        {/* Dual Micro Segmented Controls (Theme & Lang) */}
        <div className="kn-micro-control-strip">
          {/* Appearance Toggle */}
          <div className="kn-segmented-pill" role="group" aria-label={t("app.theme")}>
            <button
              type="button"
              className={`kn-segmented-pill-btn ${theme === "light" ? "active" : ""}`}
              onClick={() => setTheme("light")}
              aria-pressed={theme === "light"}
              title={t("app.themeLight")}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5" />
                <line x1="12" y1="1" x2="12" y2="3" />
                <line x1="12" y1="21" x2="12" y2="23" />
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                <line x1="1" y1="12" x2="3" y2="12" />
                <line x1="21" y1="12" x2="23" y2="12" />
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
              </svg>
              <span>Light</span>
            </button>
            <button
              type="button"
              className={`kn-segmented-pill-btn ${theme === "dark" ? "active" : ""}`}
              onClick={() => setTheme("dark")}
              aria-pressed={theme === "dark"}
              title={t("app.themeDark")}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
              <span>Dark</span>
            </button>
          </div>

        </div>
      </nav>

      {/* ====================================================================
          Main Column: Top Command Bar + Content Stage
         ==================================================================== */}
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        
        {/* Top Command Bar */}
        <header className="kn-top-command-bar">
          {/* 汉堡只在窄屏出现（CSS 控制），宽屏下侧栏常驻，按钮是多余的 */}
          <button
            className="kn-hamburger"
            aria-label={t("nav.openMenu")}
            aria-expanded={drawerOpen}
            onClick={() => setDrawerOpen((open) => !open)}
          >
            ☰
          </button>
          {/* Breadcrumbs */}
          <div className="kn-breadcrumbs">
            <span style={{ opacity: 0.6 }}>GRC</span>
            <span className="kn-breadcrumb-separator">/</span>
            <span className="kn-breadcrumb-item">{breadcrumb.section}</span>
            {breadcrumb.page && (
              <>
                <span className="kn-breadcrumb-separator">/</span>
                <span className="kn-breadcrumb-current">{breadcrumb.page}</span>
              </>
            )}
          </div>

          {/* Right Actions & Status Indicators */}
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {/* Quick ⌘K Search Prompt */}
            <button
              type="button"
              onClick={() => navigate("/search")}
              className="kn-cmd-search-btn"
              title={t("topbar.quickSearch")}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <span>{t("topbar.searchPlaceholder").split(" (")[0]}</span>
              <kbd className="kn-cmd-kbd">⌘K</kbd>
            </button>

            {/* Pending Reviews Pill (if tasks exist) */}
            {pendingCount > 0 ? (
              <NavLink to="/review" className="kn-pending-pill" title={t("topbar.pendingReviews", { count: pendingCount })}>
                <span className="kn-pulse-dot" style={{ background: "#f59e0b", boxShadow: "0 0 8px #f59e0b" }} />
                <span>{t("topbar.pendingReviews", { count: pendingCount })}</span>
              </NavLink>
            ) : null}

            {/* System Status Heartbeat —— 状态来自 /api/health，不是常量。
                别给它加 role="status"：各页面的 e2e 都用 getByRole("status") 取
                自己那条操作结果提示，顶栏常驻一个同角色的元素会让它们全部
                撞上 strict mode violation（2026-09-19 加过一次，挂了 5 条）。 */}
            <div
              className={`kn-heartbeat-pill${healthQuery.isPending ? " kn-heartbeat-pill--checking" : healthOk ? "" : " kn-heartbeat-pill--down"}`}
              title={healthOk ? undefined : t("topbar.systemDegradedHint")}
            >
              <span className="kn-pulse-dot" />
              <span>{healthLabel}</span>
            </div>
          </div>
        </header>

        {/* Content Canvas */}
        <main
          style={{
            flex: 1,
            padding: "32px 40px 48px",
            maxWidth: "1440px",
            width: "100%",
            boxSizing: "border-box",
            position: "relative",
            zIndex: 1,
          }}
        >
          <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem", marginBottom: 16 }}>{t("preview.notice")}</p>
          <PageErrorBoundary>
            <Outlet />
          </PageErrorBoundary>
        </main>
      </div>
    </div>
  );
}
