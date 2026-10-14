import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { request } from "../../api";
import { Card, DataTable } from "../../components/ui";

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
    <Card>
      <div className="kn-mb-4">
        <h3 className="kn-card-title-sm" style={{ fontSize: "1.125rem" }}>{t("settings.auditLog")}</h3>
        <p className="kn-page-email">{t("settings.auditLogConfig.description")}</p>
      </div>

      {/* 加载失败时空表读起来是「没有任何审计记录」；只取最近 LIMIT 条时也要说出来 */}
      {error && <p role="alert">⚠️ {error.message}</p>}
      {data && data.length >= LIMIT && (
        <p className="kn-muted kn-text-xs" role="note">
          {t("settings.auditLogConfig.latestOnly", { count: LIMIT })}
        </p>
      )}
      <DataTable className="kn-m-0" head={<><tr>
              <th>{t("settings.auditLogConfig.time")}</th>
              <th>{t("settings.auditLogConfig.user")}</th>
              <th>{t("settings.auditLogConfig.action")}</th>
              <th>{t("settings.auditLogConfig.entity")}</th>
              <th>{t("settings.auditLogConfig.change")}</th>
            </tr></>}>
            {data?.map((e) => (
              <tr key={e.id}>
                <td className="kn-audit-time">
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
                <td className="kn-audit-change">
                  {e.before && (
                    <div className="kn-diff-before">
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
          </DataTable>
    </Card>
  );
}
