import { Navigate, NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { canManageLlmConfig, canReadAuditLog, canWriteEvidence, useAuth } from "../../auth";

export function SettingsLayout() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const isAdmin = canManageLlmConfig(user?.role);

  return (
    <section>
      <div style={{ marginBottom: 20 }}>
        <h2>{t("settings.title")}</h2>
        <p style={{ margin: 0, fontSize: "0.875rem", color: "var(--text-secondary)" }}>
          {t("settings.subtitle")}
        </p>
      </div>

      <nav
        className="kn-segmented"
        style={{
          marginBottom: 24,
          flexWrap: "wrap",
          padding: 5,
        }}
      >
        {isAdmin && (
          <NavLink
            to="providers"
            className={({ isActive }) => `kn-segmented-item ${isActive ? "active" : ""}`}
          >
            {t("settings.providers")}
          </NavLink>
        )}
        {isAdmin && (
          <NavLink
            to="redaction"
            className={({ isActive }) => `kn-segmented-item ${isActive ? "active" : ""}`}
          >
            {t("settings.redaction")}
          </NavLink>
        )}
        {isAdmin && (
          <NavLink
            to="thresholds"
            className={({ isActive }) => `kn-segmented-item ${isActive ? "active" : ""}`}
          >
            {t("settings.thresholds")}
          </NavLink>
        )}
        {isAdmin && (
          <NavLink
            to="users"
            className={({ isActive }) => `kn-segmented-item ${isActive ? "active" : ""}`}
          >
            {t("settings.users")}
          </NavLink>
        )}
        {canWriteEvidence(user?.role) && (
          <NavLink
            to="evidence-types"
            className={({ isActive }) => `kn-segmented-item ${isActive ? "active" : ""}`}
          >
            {t("settings.evidenceTypes")}
          </NavLink>
        )}
        {canReadAuditLog(user?.role) && (
          <NavLink
            to="audit-log"
            className={({ isActive }) => `kn-segmented-item ${isActive ? "active" : ""}`}
          >
            {t("settings.auditLog")}
          </NavLink>
        )}
      </nav>

      <Outlet />
    </section>
  );
}

/**
 * `/settings` 落到这个角色能进的第一个标签。
 *
 * 原先写死跳 `providers`——加了路由守卫之后，contributor 会被守卫再弹回首页，
 * 等于"设置页对他不存在"，而他其实能维护证据类型。
 */
export function SettingsHome() {
  const { user } = useAuth();
  if (canManageLlmConfig(user?.role)) return <Navigate to="providers" replace />;
  if (canWriteEvidence(user?.role)) return <Navigate to="evidence-types" replace />;
  if (canReadAuditLog(user?.role)) return <Navigate to="audit-log" replace />;
  return <Navigate to="/" replace />;
}
