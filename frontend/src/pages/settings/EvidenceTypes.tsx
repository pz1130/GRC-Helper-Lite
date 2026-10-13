import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { request } from "../../api";

const CADENCES = ["monthly", "quarterly", "semiannual", "annual", "ad_hoc"] as const;
type Cadence = (typeof CADENCES)[number];

interface EvidenceType {
  id: number;
  name_zh: string;
  name_en: string;
  format: string;
  cadence: Cadence;
  typical_source: string;
  description: string;
}

const EMPTY = {
  name_zh: "",
  name_en: "",
  format: "",
  cadence: "quarterly" as Cadence,
  typical_source: "",
  description: "",
};

/**
 * 证据类型维护。
 *
 * 后端 `POST /api/evidence-types` 一直都在，但前端从来没有界面能建它，而证据
 * 登记的保存按钮必须有 `evidence_type_id`——于是新部署的第一天，证据登记是死的。
 * 生产栈实测 `evidence_types` 表为 0 行，只能绕过接口用 SQL 插进去（那样连
 * 审计都不留）。这一页就是补上那个缺口。
 *
 * 权限用 EVIDENCE_WRITE 而不是 admin：这是参照数据，谁能登记证据谁就该能维护它。
 */
export function EvidenceTypes() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ ...EMPTY });
  const [editingId, setEditingId] = useState<number | null>(null);
  const [error, setError] = useState("");

  const types = useQuery({
    queryKey: ["evidence-types"],
    queryFn: () => request<EvidenceType[]>("/api/evidence-types"),
  });

  function done() {
    setForm({ ...EMPTY });
    setEditingId(null);
    setError("");
    void queryClient.invalidateQueries({ queryKey: ["evidence-types"] });
  }

  const save = useMutation({
    mutationFn: () =>
      request<EvidenceType>(
        editingId === null ? "/api/evidence-types" : `/api/evidence-types/${editingId}`,
        { method: editingId === null ? "POST" : "PATCH", body: JSON.stringify(form) },
      ),
    onSuccess: done,
    onError: (e: Error) => setError(e.message),
  });

  // 被证据引用的类型删不掉，后端会说明还有哪些在用它——把那句话原样显示出来。
  const remove = useMutation({
    mutationFn: (id: number) => request(`/api/evidence-types/${id}`, { method: "DELETE" }),
    onSuccess: done,
    onError: (e: Error) => setError(e.message),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    save.mutate();
  }

  return (
    <div style={{ display: "grid", gap: 20 }}>
      <div className="kn-card">
        <h3 style={{ marginTop: 0 }}>{t("evidenceTypes.title")}</h3>
        <p style={{ margin: "0 0 16px", fontSize: "0.875rem", color: "var(--text-secondary)" }}>
          {t("evidenceTypes.subtitle")}
        </p>
        <div className="kn-table-container" style={{ margin: 0 }}>
          <table>
            <thead>
              <tr>
                <th>{t("evidenceTypes.nameEn")}</th>
                <th>{t("evidenceTypes.format")}</th>
                <th>{t("evidenceTypes.cadence")}</th>
                <th>{t("evidenceTypes.source")}</th>
                <th style={{ textAlign: "right" }}>{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {types.data?.length === 0 && (
                <tr>
                  <td colSpan={5} style={{ color: "var(--text-tertiary)" }}>
                    {t("evidenceTypes.empty")}
                  </td>
                </tr>
              )}
              {types.data?.map((item) => (
                <tr key={item.id}>
                  <td>{item.name_en}</td>
                  <td><code>{item.format || "—"}</code></td>
                  <td>{t(`evidenceTypes.cadences.${item.cadence}`, { defaultValue: item.cadence })}</td>
                  <td>{item.typical_source || "—"}</td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      className="kn-btn-secondary kn-btn-sm"
                      onClick={() => {
                        setEditingId(item.id);
                        setError("");
                        setForm({
                          name_zh: item.name_zh,
                          name_en: item.name_en,
                          format: item.format,
                          cadence: item.cadence,
                          typical_source: item.typical_source,
                          description: item.description,
                        });
                      }}
                    >
                      {t("common.edit")}
                    </button>{" "}
                    <button
                      className="kn-btn-danger kn-btn-sm"
                      disabled={remove.isPending}
                      onClick={() => {
                        // 删除是真删，不能撤回（在用的类型后端会拒绝）。
                        if (window.confirm(t("evidenceTypes.confirmDelete", { name: item.name_en || item.name_zh }))) remove.mutate(item.id);
                      }}
                    >
                      {t("common.delete")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <form className="kn-card" onSubmit={submit}>
        <h3 style={{ marginTop: 0 }}>
          {t(editingId === null ? "evidenceTypes.add" : "evidenceTypes.edit")}
        </h3>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
          <label>
            {t("evidenceTypes.nameEn")}
            <input required value={form.name_en} onChange={(e) => setForm({ ...form, name_en: e.target.value })} />
          </label>
          <label>
            {t("evidenceTypes.format")}
            <input value={form.format} placeholder="pdf / log / xlsx" onChange={(e) => setForm({ ...form, format: e.target.value })} />
          </label>
          <label>
            {t("evidenceTypes.cadence")}
            <select value={form.cadence} onChange={(e) => setForm({ ...form, cadence: e.target.value as Cadence })}>
              {CADENCES.map((value) => (
                <option key={value} value={value}>
                  {t(`evidenceTypes.cadences.${value}`, { defaultValue: value })}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("evidenceTypes.source")}
            <input value={form.typical_source} onChange={(e) => setForm({ ...form, typical_source: e.target.value })} />
          </label>
          <label style={{ gridColumn: "1 / -1" }}>
            {t("evidenceTypes.description")}
            <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </label>
        </div>
        {error && <p role="alert" style={{ color: "var(--accent-ruby)" }}>⚠️ {error}</p>}
        <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
          <button className="kn-btn-primary" type="submit" disabled={save.isPending || !form.name_en.trim()}>
            {t("common.save")}
          </button>
          {editingId !== null && (
            <button type="button" className="kn-btn-secondary" onClick={done}>
              {t("common.cancel")}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
