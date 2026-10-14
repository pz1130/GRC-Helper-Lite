import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { PageErrorBoundary } from "./ErrorBoundary";
import { BrandLogo } from "./BrandLogo";
import { useNavModel } from "./nav";
import { HealthPill, PendingPill, ThemeToggle, UserChip } from "./ShellParts";

/**
 * 外壳 B · Editorial：报头 + 两级标签导航，内容居中限宽。
 * 制度与审计材料是「读」出来的，限宽正文和强排版让条款、理由这类长文本
 * 更好读；一级标签是业务域，二级标签只列当前域的页面，导航不再一屏铺满。
 */
export function Layout() {
  const { t } = useTranslation();
  const { navGroups, pendingCount, healthLabel, healthState } = useNavModel();
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

  // 当前路由属于哪个业务域；没匹配上（如 /account）就落在第一个域。
  const activeGroup = useMemo(() => {
    const path = location.pathname;
    return (
      navGroups.find((group) =>
        group.items.some((item) => (item.to === "/" ? path === "/" : path === item.to || path.startsWith(item.to + "/"))),
      ) ?? navGroups[0]
    );
  }, [location.pathname, navGroups]);

  return (
    <div className="ed-shell">
      <header className="ed-masthead">
        <div className="ed-masthead-inner">
          <NavLink to="/" className="ed-wordmark">
            <BrandLogo size={30} />
            <span>
              <span className="ed-wordmark-name">{t("app.name")}</span>
              <span className="ed-wordmark-tag">{t("app.tagline")}</span>
            </span>
          </NavLink>
          <div className="ed-masthead-actions">
            <button type="button" className="ed-search" onClick={() => navigate("/search")} title={t("topbar.quickSearch")}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <span>{t("topbar.searchPlaceholder").split(" (")[0]}</span>
              <kbd className="kn-cmd-kbd">⌘K</kbd>
            </button>
            <PendingPill count={pendingCount} />
            <HealthPill state={healthState} label={healthLabel} hint={t("topbar.systemDegradedHint")} />
            <ThemeToggle compact />
            <UserChip showName={false} />
            <button
              type="button"
              className="ed-menu-btn"
              aria-label={t("nav.openMenu")}
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
            >
              ☰
            </button>
          </div>
        </div>
        <nav className={`ed-tabs${menuOpen ? " ed-tabs--open" : ""}`} aria-label={t("app.name")}>
          <div className="ed-tabs-inner">
            {navGroups.map((group) => (
              <NavLink
                key={group.id}
                to={group.items[0].to}
                end={group.items[0].to === "/"}
                className={`ed-tab${group.id === activeGroup.id ? " active" : ""}`}
                aria-current={group.id === activeGroup.id ? "true" : undefined}
              >
                {group.title}
                {group.items.some((item) => item.badge) ? <span className="ed-tab-dot" aria-hidden="true" /> : null}
              </NavLink>
            ))}
          </div>
        </nav>
        <nav className="ed-subtabs" aria-label={activeGroup.title}>
          <div className="ed-tabs-inner">
            {activeGroup.items.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => `ed-subtab${isActive ? " active" : ""}`}>
                {item.label}
                {item.badge != null ? <span className="ed-count">{item.badge}</span> : null}
              </NavLink>
            ))}
          </div>
        </nav>
      </header>
      <main className="kn-shell-main ed-main">
        <PageErrorBoundary>
          <Outlet />
        </PageErrorBoundary>
      </main>
      <footer className="ed-footer">{t("preview.notice")}</footer>
    </div>
  );
}
