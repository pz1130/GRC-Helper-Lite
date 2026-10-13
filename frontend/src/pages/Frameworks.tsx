import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { ApiError, demoNotCovered, getToken, isDemoSession, request, setToken } from "../api";
import { useAuth } from "../auth";
import { type Issue, issueText } from "../i18n";
import i18n from "../i18n";

interface Framework {
  id: number;
  key: string;
  name_zh: string;
  name_en: string;
  version: string;
  source: string;
  item_count: number;
  imported_at: string;
}

interface ValidationResult {
  ok: boolean;
  report: Issue[];
  items: number;
}

async function upload<T>(path: string, body: FormData): Promise<T> {
  // 上传/下载不走 request()，所以演示拦截也管不到这里——**必须自己挡一次**。
  // 不挡就会带着 demo-mock-token 打到真后端，401 之后 token 被清掉，人被踢回
  // 登录页（2026-09-20 测试报告的 BUG-21，以及那句"令牌无效或已过期"）。
  if (isDemoSession()) throw demoNotCovered();
  const token = getToken();
  const response = await fetch(path, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body,
  });
  if (response.status === 401) setToken(null);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, data.code ?? "error", data.message ?? i18n.t("common.requestFailed"), data.params);
  }
  return data as T;
}

export function Frameworks() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const client = useQueryClient();
  const canWrite = user?.role === "admin" || user?.role === "grc_lead";
  const [file, setFile] = useState<File | null>(null);
  const [key, setKey] = useState("");
  const [nameEn, setNameEn] = useState("");
  const [version, setVersion] = useState("");
  const [source, setSource] = useState("");
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const frameworks = useQuery({
    queryKey: ["frameworks"],
    queryFn: () => request<Framework[]>("/api/frameworks"),
  });

  function formData(): FormData {
    if (!file) throw new Error(t("frameworks.chooseFile"));
    const form = new FormData();
    form.append("file", file);
    return form;
  }

  const validate = useMutation({
    mutationFn: () => upload<ValidationResult>("/api/frameworks/validate", formData()),
    onMutate: () => { setError(""); setNotice(""); setValidation(null); },
    onError: (caught: Error) => setError(caught.message),
    onSuccess: setValidation,
  });

  const importFramework = useMutation({
    mutationFn: () => {
      const form = formData();
      form.append("key", key.trim());
      form.append("name_en", nameEn.trim());
      form.append("version", version.trim());
      form.append("source", source.trim());
      return upload<Framework>("/api/frameworks/import", form);
    },
    onMutate: () => { setError(""); setNotice(""); },
    onError: (caught: Error) => setError(caught.message),
    onSuccess: async (framework) => {
      setNotice(t("frameworks.imported", { key: framework.key }));
      setValidation(null);
      setFile(null);
      await client.invalidateQueries({ queryKey: ["frameworks"] });
    },
  });

  const busy = validate.isPending || importFramework.isPending;
  const metadataReady = Boolean(file && key.trim() && nameEn.trim() && version.trim());

  return (
    <section>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 20 }}>
        <div>
          <h2>{t("frameworks.title")}</h2>
          <p style={{ margin: 0, fontSize: "0.875rem", color: "var(--text-secondary)" }}>
            {t("frameworks.subtitle")}
          </p>
        </div>
      </div>

      {frameworks.isPending && <p role="status">{t("common.loading")}</p>}
      {frameworks.error && <p role="alert"><span>⚠️</span> {frameworks.error.message}</p>}
      {frameworks.data && frameworks.data.length === 0 && (
        <div className="kn-card" style={{ textAlign: "center", padding: "40px 20px", marginBottom: 20 }}>
          <p style={{ color: "var(--text-secondary)", margin: 0 }}>{t("common.empty")}</p>
        </div>
      )}

      {!!frameworks.data?.length && (
        <div className="kn-table-container" style={{ marginBottom: 24 }}>
          <table>
            <thead>
              <tr>
                <th>{t("frameworks.name")}</th>
                <th>{t("frameworks.key")}</th>
                <th>{t("frameworks.version")}</th>
                <th>{t("frameworks.items")}</th>
              </tr>
            </thead>
            <tbody>
              {frameworks.data.map((framework) => (
                <tr key={framework.id}>
                  <td>
                    <Link
                      to={`/frameworks/${framework.id}`}
                      style={{ fontWeight: 600, color: "var(--text-primary)" }}
                    >
                      {framework.name_en || framework.name_zh}
                    </Link>
                  </td>
                  <td>
                    <code>{framework.key}</code>
                  </td>
                  <td>
                    <span className="kn-badge">{framework.version}</span>
                  </td>
                  <td>
                    <span className="kn-badge kn-badge-blue">
                      {framework.item_count} {t("frameworks.items")}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {canWrite && (
        <details className="kn-card" open style={{ marginTop: 24 }}>
          <summary style={{ fontSize: "1rem", color: "var(--text-primary)" }}>
            {t("frameworks.import")}
          </summary>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.875rem", margin: "8px 0 16px 0" }}>
            {t("frameworks.importHint")}
          </p>

          <div style={{ marginBottom: 16 }}>
            <label style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
              <span>{t("frameworks.file")}</span>
              <input
                type="file"
                accept=".xlsx"
                disabled={busy}
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null);
                  setValidation(null);
                  setError("");
                  setNotice("");
                }}
              />
            </label>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 14, maxWidth: 680, marginBottom: 18 }}>
            <label>
              {t("frameworks.key")}
              <input value={key} disabled={busy} onChange={(event) => setKey(event.target.value)} />
            </label>
            <label>
              {t("frameworks.nameEn")}
              <input value={nameEn} disabled={busy} onChange={(event) => setNameEn(event.target.value)} />
            </label>
            <label>
              {t("frameworks.version")}
              <input value={version} disabled={busy} onChange={(event) => setVersion(event.target.value)} />
            </label>
            <label style={{ gridColumn: "1 / -1" }}>
              {t("frameworks.source")}
              <input value={source} disabled={busy} onChange={(event) => setSource(event.target.value)} />
            </label>
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
            <button
              className="kn-btn-secondary kn-btn-sm"
              disabled={!file || busy}
              onClick={() => validate.mutate()}
            >
              {t("frameworks.validate")}
            </button>
            <button
              className="kn-btn-primary kn-btn-sm"
              disabled={!validation?.ok || !metadataReady || busy}
              onClick={() => importFramework.mutate()}
            >
              {t("frameworks.confirmImport")}
            </button>
          </div>

          {busy && <p role="status">{t("common.loading")}</p>}
          {error && <p role="alert"><span>⚠️</span> {error}</p>}
          {notice && <p role="status">{notice}</p>}

          {validation && (
            <div role={validation.ok ? "status" : "alert"} style={{ marginTop: 14 }}>
              <p style={{ margin: "0 0 6px 0", fontWeight: 600 }}>
                {t("frameworks.report")} · {validation.items} {t("frameworks.items")}
              </p>
              {validation.report.length > 0 && (
                <ul style={{ paddingLeft: 18, margin: 0, fontSize: "0.8125rem" }}>
                  {validation.report.map((issue, index) => (
                    <li key={index}>{issueText(issue)}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </details>
      )}
    </section>
  );
}
