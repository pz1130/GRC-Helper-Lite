import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { PageErrorBoundary } from "./ErrorBoundary";
import { BrandLogo } from "./BrandLogo";
import { useNavModel } from "./nav";
import { HealthPill, PendingPill, ThemeToggle, UserChip } from "./ShellParts";

/**
 * 外壳 A · Command Center：顶部横向导航 + 状态带，内容区铺满宽屏。
 * 风险团队在大屏上盯指标与队列，横向导航把侧栏那 240px 还给表格；
 * 分组标题留在导航里做分隔，让四个业务域仍然一眼可分。
 */
export function Layout() {
  const { t } = useTranslation();
  const { navGroups, pendingCount, healthLabel, healthState, breadcrumb } = useNavModel();
  const location = useLocation();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        navigate("/search");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  useEffect(() => setMenuOpen(false), [location.pathname]);

  return (
    <div className="cc-shell">
      <header className="cc-topbar">
        <div className="cc-brand">
          <BrandLogo size={26} />
          <span className="cc-brand-name">{t("app.name")}</span>
        </div>
        <button
          type="button"
          className="cc-menu-btn"
          aria-label={t("nav.openMenu")}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          ☰
        </button>
        <nav className={`cc-nav${menuOpen ? " cc-nav--open" : ""}`} aria-label={t("app.name")}>
          {navGroups.map((group) => (
            <div key={group.id} className="cc-nav-group" role="group" aria-label={group.title}>
              <span className="cc-nav-group-title">{group.title}</span>
              {group.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => `cc-nav-item${isActive ? " active" : ""}`}>
                  <span className="cc-nav-icon" aria-hidden="true">{item.icon}</span>
                  <span>{item.label}</span>
                  {item.badge != null ? <span className="cc-nav-badge">{item.badge}</span> : null}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="cc-actions">
          <button type="button" className="cc-search" onClick={() => navigate("/search")} title={t("topbar.quickSearch")}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <kbd className="kn-cmd-kbd">⌘K</kbd>
          </button>
          <ThemeToggle compact />
          <UserChip showName={false} />
        </div>
      </header>
      <div className="cc-statusbar">
        <div className="cc-crumbs">
          <span className="cc-crumb-section">{breadcrumb.section}</span>
          {breadcrumb.page ? (
            <>
              <span aria-hidden="true">›</span>
              <span className="cc-crumb-page">{breadcrumb.page}</span>
            </>
          ) : null}
        </div>
        <p className="cc-notice">{t("preview.notice")}</p>
        <div className="cc-status-pills">
          <PendingPill count={pendingCount} />
          <HealthPill state={healthState} label={healthLabel} hint={t("topbar.systemDegradedHint")} />
        </div>
      </div>
      <main className="kn-shell-main cc-main">
        <PageErrorBoundary>
          <Outlet />
        </PageErrorBoundary>
      </main>
    </div>
  );
}
