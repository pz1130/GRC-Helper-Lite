import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { PageErrorBoundary } from "./ErrorBoundary";
import { BrandLogo } from "./BrandLogo";
import { CommandPalette } from "./CommandPalette";
import { useNavModel } from "./nav";
import { HealthPill, PendingPill, ThemeToggle, UserChip } from "./ShellParts";

const PANEL_KEY = "grc.ws.panel";

/**
 * 外壳 C · Workspace：图标轨 + 可收起的上下文面板 + 命令面板。
 * 图标轨常驻、只占 56px；上下文面板列出当前业务域的页面，收起后把宽度
 * 全给内容。⌘K 打开命令面板（跳页），不再直接跳到搜索页。
 */
export function Layout() {
  const { t } = useTranslation();
  const { navGroups, pendingCount, healthLabel, healthState, breadcrumb } = useNavModel();
  const location = useLocation();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(() => localStorage.getItem(PANEL_KEY) !== "closed");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => setDrawerOpen(false), [location.pathname]);

  const togglePanel = () => {
    setPanelOpen((open) => {
      localStorage.setItem(PANEL_KEY, open ? "closed" : "open");
      return !open;
    });
  };

  const activeGroup = useMemo(() => {
    const path = location.pathname;
    return (
      navGroups.find((group) =>
        group.items.some((item) => (item.to === "/" ? path === "/" : path === item.to || path.startsWith(item.to + "/"))),
      ) ?? navGroups[0]
    );
  }, [location.pathname, navGroups]);

  return (
    <div className={`ws-shell${panelOpen ? "" : " ws-shell--collapsed"}${drawerOpen ? " ws-shell--drawer" : ""}`}>
      <nav className="ws-rail" aria-label={t("app.name")}>
        <NavLink to="/" className="ws-rail-brand" aria-label={t("app.name")}>
          <BrandLogo size={28} />
        </NavLink>
        <div className="ws-rail-items">
          {navGroups.map((group) => (
            <div key={group.id} className="ws-rail-group" role="group" aria-label={group.title}>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) => `ws-rail-item${isActive ? " active" : ""}`}
                  aria-label={item.label}
                  data-tip={item.label}
                >
                  {item.icon}
                  {item.badge != null ? <span className="ws-rail-badge">{item.badge}</span> : null}
                </NavLink>
              ))}
            </div>
          ))}
        </div>
        <div className="ws-rail-foot">
          <UserChip showName={false} />
        </div>
      </nav>

      <aside className="ws-panel" aria-label={activeGroup.title}>
        <div className="ws-panel-head">
          <span className="ws-panel-title">{activeGroup.title}</span>
        </div>
        <button type="button" className="ws-cmd" onClick={() => setPaletteOpen(true)}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <span>{t("topbar.quickSearch")}</span>
          <kbd className="kn-cmd-kbd">⌘K</kbd>
        </button>
        <div className="ws-panel-list">
          {activeGroup.items.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => `ws-panel-item${isActive ? " active" : ""}`}>
              <span className="ws-panel-icon" aria-hidden="true">{item.icon}</span>
              <span className="ws-panel-label">{item.label}</span>
              {item.badge != null ? <span className="ws-panel-count">{item.badge}</span> : null}
            </NavLink>
          ))}
        </div>
        <div className="ws-panel-section">
          <span className="ws-panel-subtitle">{t("nav.review")}</span>
          <PendingPill count={pendingCount} />
        </div>
        <div className="ws-panel-foot">
          <HealthPill state={healthState} label={healthLabel} hint={t("topbar.systemDegradedHint")} />
          <ThemeToggle compact />
        </div>
      </aside>

      <div className="ws-content">
        <header className="ws-topbar">
          <button
            type="button"
            className="ws-icon-btn ws-panel-toggle"
            onClick={togglePanel}
            aria-label={t("nav.openMenu")}
            aria-pressed={panelOpen}
            title={t("nav.openMenu")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <path d="M9 4v16" />
            </svg>
          </button>
          <button
            type="button"
            className="ws-icon-btn ws-drawer-toggle"
            onClick={() => setDrawerOpen((open) => !open)}
            aria-label={t("nav.openMenu")}
            aria-expanded={drawerOpen}
          >
            ☰
          </button>
          <div className="ws-crumbs">
            <span>{breadcrumb.section}</span>
            {breadcrumb.page ? (
              <>
                <span aria-hidden="true">/</span>
                <strong>{breadcrumb.page}</strong>
              </>
            ) : null}
          </div>
          <p className="ws-notice">{t("preview.notice")}</p>
        </header>
        <main className="kn-shell-main ws-main">
          <PageErrorBoundary>
            <Outlet />
          </PageErrorBoundary>
        </main>
      </div>
      {drawerOpen ? <div className="kn-drawer-backdrop" onClick={() => setDrawerOpen(false)} /> : null}
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} groups={navGroups} />
    </div>
  );
}
