import { setMotionPref, useMotionPref, type MotionPref } from "../motion";
import { useEffect, useRef, useState } from "react";
// 各外壳共用的小部件：用户卡、主题切换、心跳胶囊、待办胶囊。
// 心跳不加 role="status"：各页面的 e2e 用 getByRole("status") 取自己那条
// 操作结果提示，顶栏常驻一个同角色元素会让它们撞上 strict mode violation。
import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { useAuth } from "../auth";
import { setLanguage, type Lang } from "../i18n";
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
          {compact ? null : <span>{value === "light" ? t("app.themeLight") : t("app.themeDark")}</span>}
        </button>
      ))}
    </div>
  );
}

/** 动画档位：System / On / Off。默认 On——见 src/motion.ts 的说明。 */
export function MotionToggle() {
  const { t } = useTranslation();
  const pref = useMotionPref();
  const opts: { value: MotionPref; label: string }[] = [
    { value: "system", label: t("app.motionSystem") },
    { value: "on", label: t("app.motionOn") },
    { value: "off", label: t("app.motionOff") },
  ];
  return (
    <div className="kn-segmented-pill ws-pref-seg" role="group" aria-label={t("app.animations")}>
      {opts.map((o) => (
        <button
          key={o.value}
          type="button"
          className={`kn-segmented-pill-btn ${pref === o.value ? "active" : ""}`}
          aria-pressed={pref === o.value}
          onClick={() => setMotionPref(o.value)}
        >
          {o.label}
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
          <span className="kn-user-chip-role">{user?.role ? t(`settings.usersConfig.roles.${user.role}`, { defaultValue: user.role }) : null}</span>
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

/** 图标轨底部：单图标主题切换 + 偏好浮层（外观、动画、账户、退出）。 */
export function RailControls() {
  const { t } = useTranslation();
  const { theme, toggleTheme } = useTheme();
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const next = theme === "dark" ? t("app.themeLight") : t("app.themeDark");
  return (
    <div className="ws-rc" ref={ref}>
      <button type="button" className="ws-rc-btn ws-rc-theme" onClick={toggleTheme} title={next} aria-label={next}>
        <span key={theme} className="ws-rc-swap" aria-hidden="true">
          {theme === "dark" ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <circle cx="12" cy="12" r="4.5" />
              <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
            </svg>
          )}
        </span>
      </button>
      <LanguageButton />
      <button
        type="button"
        className={`ws-rc-btn ws-rc-avatar${open ? " is-open" : ""}`}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={t("app.preferences")}
        title={t("app.preferences")}
      >
        {user?.name?.[0]?.toUpperCase() ?? "U"}
      </button>
      {open ? (
        <div className="ws-rc-pop" role="dialog" aria-label={t("app.preferences")}>
          <div className="ws-rc-who">
            <span className="ws-rc-who-av" aria-hidden="true">{user?.name?.[0]?.toUpperCase() ?? "U"}</span>
            <span className="ws-rc-who-text">
              <b>{user?.name}</b>
              <small>{user?.role ? t(`settings.usersConfig.roles.${user.role}`, { defaultValue: user.role }) : null}</small>
            </span>
          </div>
          <div className="ws-rc-row">
            <span>{t("app.theme")}</span>
            <ThemeToggle />
          </div>
          <div className="ws-rc-row">
            <span>{t("app.language")}</span>
            <LanguageToggle />
          </div>
          <div className="ws-rc-row">
            <span>{t("app.animations")}</span>
            <MotionToggle />
          </div>
          <div className="ws-rc-actions">
            <NavLink to="/account" className="ws-rc-link" onClick={() => setOpen(false)}>
              {t("app.account")}
            </NavLink>
            <button type="button" className="ws-rc-link ws-rc-out" onClick={logout}>
              {t("app.logout")}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** 图标轨里的语言圆钮：显示当前语言，点一下切到另一种。 */
export function LanguageButton() {
  const { t, i18n } = useTranslation();
  const lang: Lang = i18n.language === "zh" ? "zh" : "en";
  return (
    <button
      type="button"
      className="ws-rc-btn ws-rc-lang"
      onClick={() => setLanguage(lang === "zh" ? "en" : "zh")}
      title={t("app.switchLanguage")}
      aria-label={t("app.switchLanguage")}
    >
      <span key={lang} className="ws-rc-swap ws-rc-lang-label" aria-hidden="true">
        {lang === "zh" ? "中" : "EN"}
      </span>
    </button>
  );
}

/** 语言分段：English / 中文。 */
export function LanguageToggle() {
  const { t, i18n } = useTranslation();
  const lang: Lang = i18n.language === "zh" ? "zh" : "en";
  return (
    <div className="kn-segmented-pill ws-pref-seg" role="group" aria-label={t("app.language")}>
      {(["en", "zh"] as const).map((value) => (
        <button
          key={value}
          type="button"
          lang={value === "zh" ? "zh-CN" : "en"}
          className={`kn-segmented-pill-btn ${lang === value ? "active" : ""}`}
          aria-pressed={lang === value}
          onClick={() => setLanguage(value)}
        >
          {value === "zh" ? t("app.langZh") : t("app.langEn")}
        </button>
      ))}
    </div>
  );
}
