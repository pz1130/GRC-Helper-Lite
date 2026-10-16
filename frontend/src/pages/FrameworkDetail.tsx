import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { ApiError, demoNotCovered, getToken, isDemoSession, request, setToken } from "../api";
import { useAuth } from "../auth";
import i18n, { renderParams } from "../i18n";
import { Card, DataTable, EmptyState, LoadingState, PageHeader } from "../components/ui";

interface Framework {
  id: number; key: string; name_zh: string; name_en: string;
  version: string; source: string; item_count: number; imported_at: string;
}
interface FrameworkItem {
  id: number; parent_id: number | null; code: string; title: string;
  description: string; level: number; order_index: number; attributes: Record<string, unknown> | null;
}
interface CoverageRow {
  item_id: number; code: string; title: string; level: number;
  parent_id: number | null; requirements: number; covered: number;
  /** 已覆盖的要求里，关上它的控制点出处全在已替换文档里的条数。仍计入 covered。 */
  superseded_only?: number;
}
interface SupersededRow { item_id: number; code: string; title: string; controls: string[] }
interface GapRow { item_id: number; code: string; title: string; has_supporting: boolean; }

function filenameFromDisposition(header: string | null): string {
  const match = header?.match(/filename\*?=(?:UTF-8'')?["']?([^";]+)/i);
  return match?.[1] ? decodeURIComponent(match[1]) : "readiness.zip";
}

async function downloadReadinessPackage(frameworkId: string): Promise<{ blob: Blob; filename: string }> {
  // 上传/下载不走 request()，所以演示拦截也管不到这里——**必须自己挡一次**。
  // 不挡就会带着 demo-mock-token 打到真后端，401 之后 token 被清掉，人被踢回
  // 登录页（2026-09-20 测试报告的 BUG-21，以及那句"令牌无效或已过期"）。
  if (isDemoSession()) throw demoNotCovered();
  const token = getToken();
  const response = await fetch(`/api/frameworks/${frameworkId}/readiness-package?language=en`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (response.status === 401) {
    setToken(null);
    throw new ApiError(401, "unauthorized", i18n.t("common.unauthorized"));
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.code ?? "error", body.message ?? i18n.t("common.requestFailed"), body.params);
  }
  return {
    blob: await response.blob(),
    filename: filenameFromDisposition(response.headers.get("Content-Disposition")),
  };
}

export function FrameworkDetail() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { id } = useParams();
  const [baseline, setBaseline] = useState("");
  const canWrite = user?.role === "admin" || user?.role === "grc_lead";
  const suffix = baseline ? `?baseline=${encodeURIComponent(baseline)}` : "";
  const framework = useQuery({
    queryKey: ["frameworks"], queryFn: () => request<Framework[]>("/api/frameworks"),
  });
  const tree = useQuery({
    queryKey: ["framework-tree", id], queryFn: () => request<FrameworkItem[]>(`/api/frameworks/${id}/tree`),
    enabled: Boolean(id),
  });
  const coverage = useQuery({
    queryKey: ["framework-coverage", id, baseline],
    queryFn: () => request<CoverageRow[]>(`/api/frameworks/${id}/coverage${suffix}`),
    enabled: Boolean(id),
  });
  const gaps = useQuery({
    queryKey: ["framework-gaps", id, baseline],
    queryFn: () => request<GapRow[]>(`/api/frameworks/${id}/gaps${suffix}`),
    enabled: Boolean(id),
  });
  const superseded = useQuery({
    queryKey: ["framework-superseded", id, baseline],
    queryFn: () => request<SupersededRow[]>(`/api/frameworks/${id}/superseded-support${suffix}`),
    enabled: Boolean(id),
  });
  const runMapping = useMutation({
    mutationFn: () => request<{ job_id: string }>(`/api/mapping/frameworks/${id}`, { method: "POST" }),
  });
  const exportPackage = useMutation({
    mutationFn: () => downloadReadinessPackage(id!),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    },
  });
  const current = framework.data?.find((item) => String(item.id) === id);

  return (
    <section>
      <p className="kn-mb-4">
        <Link to="/frameworks" className="kn-check-label">
          ← {t("frameworks.title")}
        </Link>
      </p>

      <PageHeader
        title={
          <>
            {current ? current.name_en || current.name_zh : id}
            {current && <small className="kn-title-meta">· {current.version}</small>}
          </>
        }
        subtitle={current ? `${t("frameworks.source")}: ${current.source}` : undefined}
        actions={
          <>
          <button
            className="kn-btn-secondary"
            disabled={exportPackage.isPending}
            onClick={() => exportPackage.mutate()}
          >
            {t("frameworks.exportPackage")}
          </button>
          {canWrite && (
            <button
              className="kn-btn-primary"
              disabled={runMapping.isPending}
              onClick={() => runMapping.mutate()}
            >
              {t("frameworks.runMapping")}
            </button>
          )}
          </>
        }
      />

      {runMapping.isPending && <LoadingState inline />}
      {runMapping.error && <p role="alert"><span>⚠️</span> {runMapping.error.message}</p>}
      {runMapping.data && <p role="status">{t("frameworks.mappingQueued", { id: runMapping.data.job_id })}</p>}
      {exportPackage.isPending && <LoadingState inline />}
      {/* 带上原因：只说「导出失败」，人不知道是权限、数据还是服务的问题 */}
      {exportPackage.error && <p role="alert"><span>⚠️</span> {t("frameworks.exportFailed")}: {exportPackage.error.message}</p>}

      {/* Coverage & Gap Analysis Section */}
      <Card className="kn-mb-6">
        <div className="kn-card-toolbar">
          <h3 className="kn-m-0">{t("frameworks.coverage")}</h3>
          <label className="kn-inline-label kn-m-0">
            <span>{t("frameworks.baseline")}</span>
            <select
              value={baseline}
              onChange={(event) => setBaseline(event.target.value)}
              className="kn-select-sm"
            >
              <option value="">{t("frameworks.baselineAll")}</option>
              <option value="low">{t("frameworks.baselines.low")}</option>
              <option value="moderate">{t("frameworks.baselines.moderate")}</option>
              <option value="high">{t("frameworks.baselines.high")}</option>
            </select>
          </label>
        </div>

        {coverage.isPending && <LoadingState />}
        {coverage.error && <p role="alert"><span>⚠️</span> {coverage.error.message}</p>}

        <DataTable className="kn-m-0" head={<><tr>
                <th>{t("frameworks.item")}</th>
                <th>{t("frameworks.coverage")}</th>
              </tr></>}>
              {coverage.data?.map((row) => {
                const ratio = row.requirements ? row.covered / row.requirements : null;
                const badgeClass =
                  ratio === null
                    ? ""
                    : ratio >= 1
                    ? "kn-badge-emerald"
                    : ratio > 0
                    ? "kn-badge-amber"
                    : "kn-badge-ruby";

                return (
                  <tr key={row.item_id}>
                    <td style={{ paddingLeft: 18 + Math.max(0, row.level - 1) * 20 }}>
                      <code className="kn-mr-2">{row.code}</code>
                      <span className={row.level === 1 ? "kn-weight-1" : "kn-weight-0"}>{row.title}</span>
                    </td>
                    <td>
                      <div className="kn-row">
                        <span className={`kn-badge ${badgeClass}`}>
                          {row.covered} / {row.requirements}
                        </span>
                        {!!row.superseded_only && (
                          <span className="kn-badge kn-badge-amber" title={t("frameworks.supersededOnlyHint")}>
                            {t("frameworks.supersededOnlyCount", { count: row.superseded_only })}
                          </span>
                        )}
                        {ratio !== null && (
                          <div className="kn-coverage-bar">
                            <div className="kn-progress-bar">
                              <div
                                className="kn-progress-fill"
                                style={{
                                  width: `${Math.round(ratio * 100)}%`,
                                  background:
                                    ratio >= 1
                                      ? "var(--accent-emerald)"
                                      : ratio > 0
                                      ? "var(--accent-amber)"
                                      : "var(--accent-ruby)",
                                }}
                              />
                            </div>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </DataTable>
      </Card>

      {/* 仍算覆盖、但依据已作废：不自动降级（已确认的数据只由人改），而是单独列出来 */}
      {!!superseded.data?.length && (
        <div className="kn-card kn-card--warn-strong kn-mb-6" data-card="superseded-support">
          <h3 className="kn-card-title-sm">{t("frameworks.supersededOnly")}</h3>
          <p className="kn-card-note">
            {t("frameworks.supersededOnlyHint")}
          </p>
          <ul className="kn-list-plain">
            {superseded.data.map((row) => (
              <li key={row.item_id} className="kn-inline-list-row">
                <code>{row.code}</code>
                <span>{row.title}</span>
                <span className="kn-muted kn-text-xs">
                  {t("frameworks.supersededOnlyControls", renderParams({ controls: row.controls }))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {superseded.error && <p role="alert"><span>⚠️</span> {superseded.error.message}</p>}

      {/* Gaps Checklist */}
      <Card className="kn-mb-6" title={t("frameworks.gaps")}>
        {gaps.isPending && <LoadingState />}
        {gaps.error && <p role="alert"><span>⚠️</span> {gaps.error.message}</p>}
        {gaps.data?.length === 0 && (
          <EmptyState compact tone="success" />
        )}
        <ul className="kn-list-plain">
          {gaps.data?.map((gap) => (
            <li key={gap.item_id} className="kn-gap-row">
              <span className="kn-badge kn-badge-ruby">{t("frameworks.gap")}</span>
              <code>{gap.code}</code>
              <span className="kn-primary-text">{gap.title}</span>
              {gap.has_supporting && (
                <small className="kn-gap-row-note">
                  {t("frameworks.hasSupporting")}
                </small>
              )}
            </li>
          ))}
        </ul>
      </Card>

      {/* Framework Tree */}
      <Card title={t("frameworks.tree")}>
        {tree.isPending && <LoadingState />}
        {tree.error && <p role="alert"><span>⚠️</span> {tree.error.message}</p>}
        <ul className="kn-list-plain">
          {tree.data?.map((item) => (
            <li
              key={item.id}
              className={item.level > 1 ? "kn-tree-row kn-tree-row--nested" : "kn-tree-row"}
              // 缩进随层级变化，只能留在 style 里
              style={{ paddingLeft: Math.max(0, item.level - 1) * 20 }}
            >
              <code className="kn-mr-2">{item.code}</code>
              <span className={item.level === 1 ? "kn-weight-1" : "kn-weight-0"}>{item.title}</span>
              {item.description && (
                <span className="kn-tree-desc">
                  — {item.description}
                </span>
              )}
            </li>
          ))}
        </ul>
      </Card>
    </section>
  );
}
