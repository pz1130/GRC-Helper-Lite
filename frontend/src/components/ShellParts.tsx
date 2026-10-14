// 各外壳共用的小部件：用户卡、主题切换、心跳胶囊、待办胶囊。
// 心跳不加 role="status"：各页面的 e2e 用 getByRole("status") 取自己那条
// 操作结果提示，顶栏常驻一个同角色元素会让它们撞上 strict mode violation。
import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { useAuth } from "../auth";
import { useTheme } from "../theme";

export function HealthPill({ state, label, hint }: { state: "checking" | "ok" | "down"; label: string; hint?: string }) {
  return (
    <div
      className={`kn-heartbeat-pill${state === "checking" ? " kn-heartbeat-pill--checking" : state === "ok" ? "" : " kn-heartbeat-pill--down"}`}
      title={state === "ok" ? undefined : hint}
    >
      <span className="kn-pulse-dot" />
      <span>{label}</span>
    </div>
  );
}

export function PendingPill({ count }: { count: number }) {
  const { t } = useTranslation();
  if (count <= 0) return null;
  return (
    <NavLink to="/review" className="kn-pending-pill" title={t("topbar.pendingReviews", { count })}>
      <span className="kn-pulse-dot kn-pulse-dot--amber" />
      <span>{t("topbar.pendingReviews", { count })}</span>
    </NavLink>
  );
}

export function ThemeToggle({ compact }: { compact?: boolean }) {
  const { t } = useTranslation();
  const { theme, setTheme } = useTheme();
  return (
    <div className="kn-segmented-pill" role="group" aria-label={t("app.theme")}>
      {(["light", "dark"] as const).map((value) => (
        <button
          key={value}
          type="button"
          className={`kn-segmented-pill-btn ${theme === value ? "active" : ""}`}
          onClick={() => setTheme(value)}
          aria-pressed={theme === value}
          title={value === "light" ? t("app.themeLight") : t("app.themeDark")}
          aria-label={value === "light" ? t("app.themeLight") : t("app.themeDark")}
        >
          {value === "light" ? (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
              <circle cx="12" cy="12" r="5" />
              <path d="M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" />
            </svg>
          ) : (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
            </svg>
          )}
          {compact ? null : <span>{value === "light" ? "Light" : "Dark"}</span>}
        </button>
      ))}
    </div>
  );
}

export function UserChip({ showName = true }: { showName?: boolean }) {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  return (
    <div className="kn-user-chip">
      <NavLink to="/account" className="kn-avatar" aria-label={t("app.account")} title={t("app.account")}>
        {user?.name?.[0]?.toUpperCase() ?? "U"}
      </NavLink>
      {showName ? (
        <div className="kn-user-chip-text">
          <span className="kn-user-chip-name">{user?.name}</span>
          <span className="kn-user-chip-role">{user?.role}</span>
        </div>
      ) : null}
      <button type="button" onClick={logout} className="kn-btn-logout-compact" title={t("app.logout")} aria-label={t("app.logout")}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
          <polyline points="16 17 21 12 16 7" />
          <line x1="21" y1="12" x2="9" y2="12" />
        </svg>
        {showName ? <span>{t("app.logout")}</span> : null}
      </button>
    </div>
  );
}
