import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { request } from "../../api";

interface Entry {
  id: number;
  user_id: number | null;
  action: string;
  entity_type: string;
  entity_id: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  at: string;
}

const LIMIT = 200;

export function AuditLog() {
  const { t } = useTranslation();
  const { data, error } = useQuery({
    queryKey: ["audit-log"],
    queryFn: () => request<Entry[]>(`/api/audit-log?limit=${LIMIT}`),
  });

  return (
    <div className="kn-card">
      <div className="kn-mb-4">
        <h3 style={{ margin: "0 0 4px 0", fontSize: "1.125rem" }}>{t("settings.auditLog")}</h3>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.8125rem", margin: 0 }}>
          {t("settings.auditLogConfig.description")}
        </p>
      </div>

      {/* 加载失败时空表读起来是「没有任何审计记录」；只取最近 LIMIT 条时也要说出来 */}
      {error && <p role="alert">⚠️ {error.message}</p>}
      {data && data.length >= LIMIT && (
        <p className="kn-muted kn-text-xs" role="note">
          {t("settings.auditLogConfig.latestOnly", { count: LIMIT })}
        </p>
      )}
      <div className="kn-table-container kn-m-0">
        <table>
          <thead>
            <tr>
              <th>{t("settings.auditLogConfig.time")}</th>
              <th>{t("settings.auditLogConfig.user")}</th>
              <th>{t("settings.auditLogConfig.action")}</th>
              <th>{t("settings.auditLogConfig.entity")}</th>
              <th>{t("settings.auditLogConfig.change")}</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((e) => (
              <tr key={e.id}>
                <td style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                  {e.at.replace("T", " ").slice(0, 19)}
                </td>
                <td>
                  <span className="kn-medium">{e.user_id ?? "—"}</span>
                </td>
                <td>
                  <span className="kn-badge kn-badge-blue">
                    {e.action}
                  </span>
                </td>
                <td>
                  <code>
                    {e.entity_type}#{e.entity_id}
                  </code>
                </td>
                <td style={{ fontSize: "0.75rem", fontFamily: "monospace" }}>
                  {e.before && (
                    <div style={{ color: "var(--accent-ruby)", marginBottom: 2 }}>
                      − {JSON.stringify(e.before)}
                    </div>
                  )}
                  {e.after && (
                    <div className="kn-ok-text">
                      + {JSON.stringify(e.after)}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
