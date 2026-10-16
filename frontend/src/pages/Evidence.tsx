import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { request } from "../api";
import { useAuth } from "../auth";
import { PrerequisiteNotice } from "../components/PrerequisiteNotice";
import { CONTROL_SEARCH_LIMIT, useControlSearch, withSelected } from "../controlSearch";
import type { ControlOption } from "../controlSearch";
import { Card, DataTable, EmptyState, LoadingState, PageHeader } from "../components/ui";

interface EvidenceType {
  id: number;
  name_zh: string;
  name_en: string;
  format: string;
  cadence: string;
  typical_source: string;
  description: string;
}

interface EvidenceItem {
  id: number;
  evidence_type_id: number;
  control_id: number;
  tech_asset_id: number | null;
  title: string;
  owner_user_id: number | null;
  location_hint: string;
  last_collected_at: string | null;
  valid_until: string | null;
  file_path: string | null;
  status: string;
  intent_status: string;
  display_status: string;
  created_at: string;
  evidence_type_name: string | null;
  control_code: string | null;
  control_title: string | null;
  tech_asset_name: string | null;
}


interface EvidenceForm {
  evidence_type_id: string;
  control_id: string;
  tech_asset_id: string;
  title: string;
  owner_user_id: string;
  location_hint: string;
  last_collected_at: string;
  valid_until: string;
  file_path: string;
  status: string;
}

const emptyForm: EvidenceForm = {
  evidence_type_id: "",
  control_id: "",
  tech_asset_id: "",
  title: "",
  owner_user_id: "",
  location_hint: "",
  last_collected_at: "",
  valid_until: "",
  file_path: "",
  status: "planned",
};

const evidenceStatuses = ["planned", "collected", "missing"];

function withParam(current: URLSearchParams, key: string, value: string): URLSearchParams {
  const next = new URLSearchParams(current);
  if (value) next.set(key, value);
  else next.delete(key);
  return next;
}

// `datetime-local` 输入框读写的都是本地时间，后端给的是 UTC。直接 `iso.slice(0, 16)`
// 会把 UTC 的钟点当成本地钟点填进去，保存时再按本地解析——东八区每保存一次，
// 采集时间和有效期都往前挪 8 小时，哪怕这两个框根本没碰过。
function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const at = new Date(iso);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

// 没改过的时间原样送回：输入框只到分钟，重新换算会把秒抹掉。
function fromLocalInput(value: string, original: string | null): string | null {
  if (!value) return null;
  if (original && value === toLocalInput(original)) return original;
  return new Date(value).toISOString();
}

/** URL 里存的是 ISO 时刻；日期框要的是它在本地是哪一天。 */
function localDateOf(iso: string | null): string {
  if (!iso) return "";
  return toLocalInput(iso).slice(0, 10);
}

function optionalId(value: string): number | null {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

export function Evidence() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const canWrite = user?.role !== "viewer";
  const [form, setForm] = useState<EvidenceForm | null>(null);
  const [editing, setEditing] = useState<EvidenceItem | null>(null);
  const editingId = editing?.id ?? null;
  const [error, setError] = useState("");
  const [controlQuery, setControlQuery] = useState("");
  const [chosenControl, setChosenControl] = useState<ControlOption | null>(null);

  const queryString = params.toString();
  const evidence = useQuery({
    queryKey: ["evidence", queryString],
    queryFn: () => request<EvidenceItem[]>(`/api/evidence${queryString ? `?${queryString}` : ""}`),
  });
  const types = useQuery({
    queryKey: ["evidence-types"],
    queryFn: () => request<EvidenceType[]>("/api/evidence-types"),
    enabled: canWrite,
  });
  const controls = useControlSearch(controlQuery, canWrite && form !== null);
  // 「有没有控制点」单独问一次：拿搜索结果判断的话，搜不到就会误报「还没有控制点」。
  const anyControl = useQuery({
    queryKey: ["controls", "exists"],
    queryFn: () => request<ControlOption[]>("/api/controls?limit=1"),
    enabled: canWrite,
  });
  const controlOptions = withSelected(controls.data, chosenControl);
  const assets = useQuery({
    queryKey: ["tech-assets", "evidence-form"],
    queryFn: () => request<{ id: number; name: string }[]>("/api/tech-assets"),
    enabled: canWrite,
  });
  const save = useMutation({
    mutationFn: () => {
      if (!form) throw new Error(t("evidence.formRequired"));
      const payload = {
        evidence_type_id: Number(form.evidence_type_id),
        control_id: Number(form.control_id),
        tech_asset_id: optionalId(form.tech_asset_id),
        title: form.title,
        owner_user_id: optionalId(form.owner_user_id),
        location_hint: form.location_hint,
        last_collected_at: fromLocalInput(form.last_collected_at, editing?.last_collected_at ?? null),
        valid_until: fromLocalInput(form.valid_until, editing?.valid_until ?? null),
        file_path: form.file_path || null,
        status: form.status,
      };
      return request<EvidenceItem>(editingId === null ? "/api/evidence" : `/api/evidence/${editingId}`, {
        method: editingId === null ? "POST" : "PATCH",
        body: JSON.stringify(payload),
      });
    },
    onMutate: () => setError(""),
    onError: (caught: Error) => setError(caught.message),
    onSuccess: async () => {
      setForm(null);
      setEditing(null);
      await client.invalidateQueries({ queryKey: ["evidence"] });
      await client.invalidateQueries({ queryKey: ["evidence-stats"] });
    },
  });

  const statusFilter = params.get("status") ?? "";
  const ownerFilter = params.get("owner_user_id") ?? "";
  const dueBefore = localDateOf(params.get("due_before"));
  const dueAfter = localDateOf(params.get("due_after"));
  const typeName = useMemo(() => {
    const names = new Map((types.data ?? []).map((type) => [type.id, type.name_en || type.name_zh]));
    return (id: number) => names.get(id) ?? `#${id}`;
  }, [types.data]);

  function beginCreate() {
    setError("");
    save.reset();
    setEditing(null);
    setForm(emptyForm);
    setControlQuery("");
    setChosenControl(null);
  }

  function beginEdit(item: EvidenceItem) {
    setError("");
    save.reset();
    setEditing(item);
    setControlQuery("");
    setChosenControl({ id: item.control_id, code: item.control_code ?? `#${item.control_id}`, title: item.control_title ?? "" });
    setForm({
      evidence_type_id: String(item.evidence_type_id),
      control_id: String(item.control_id),
      tech_asset_id: item.tech_asset_id ? String(item.tech_asset_id) : "",
      title: item.title,
      owner_user_id: item.owner_user_id ? String(item.owner_user_id) : "",
      location_hint: item.location_hint,
      last_collected_at: toLocalInput(item.last_collected_at),
      valid_until: toLocalInput(item.valid_until),
      file_path: item.file_path ?? "",
      status: item.intent_status,
    });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (form?.title.trim() && form.evidence_type_id && form.control_id) save.mutate();
  }

  function setFilter(key: string, value: string) {
    setParams(withParam(params, key, value));
  }

  // 日期筛选按本地的一天：原来拼成 UTC 的 00:00 / 23:59:59，东八区选「10 月 1 日之前」
  // 会把 10 月 2 日早上 8 点前到期的也算进来。
  function setDateFilter(key: string, value: string) {
    if (!value) return setFilter(key, "");
    const [year, month, day] = value.split("-").map(Number);
    const at = key === "due_before"
      ? new Date(year, month - 1, day, 23, 59, 59, 999)
      : new Date(year, month - 1, day, 0, 0, 0, 0);
    setFilter(key, at.toISOString());
  }

  return (
    <section>
      <PageHeader
        title={t("evidence.title")} subtitle={t("evidence.subtitle")}
        actions={canWrite && <button className="kn-btn-primary kn-btn-sm" onClick={beginCreate}>{t("common.add")}</button>}
      />

      <Card className="kn-mb-5">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12, alignItems: "end" }}>
          <label>
            {t("evidence.owner")}
            <input type="number" min="1" value={ownerFilter} placeholder={t("evidence.ownerPlaceholder")} onChange={(e) => setFilter("owner_user_id", e.target.value)} />
          </label>
          <label>
            {t("evidence.status")}
            <select value={statusFilter} onChange={(e) => setFilter("status", e.target.value)}>
              <option value="">{t("evidence.allStatuses")}</option>
              {evidenceStatuses.map((value) => <option key={value} value={value}>{t(`evidence.statuses.${value}`)}</option>)}
              <option value="valid">{t("evidence.statuses.valid")}</option>
              <option value="expired">{t("evidence.statuses.expired")}</option>
            </select>
          </label>
          <label>
            {t("evidence.dueAfter")}
            <input type="date" value={dueAfter} onChange={(e) => setDateFilter("due_after", e.target.value)} />
          </label>
          <label>
            {t("evidence.dueBefore")}
            <input type="date" value={dueBefore} onChange={(e) => setDateFilter("due_before", e.target.value)} />
          </label>
          {(statusFilter || ownerFilter || dueBefore || dueAfter) && (
            <button
              className="kn-btn-secondary kn-btn-sm"
              style={{ height: 40, alignSelf: "end" }}
              onClick={() => setParams(new URLSearchParams())}
            >
              {t("evidence.clearFilters")}
            </button>
          )}
        </div>
      </Card>

      {form && canWrite && (
        <form className="kn-card kn-form-card" onSubmit={submit}>
          <h3 className="kn-mb-4">{t(editingId === null ? "evidence.add" : "evidence.edit")}</h3>
          <div className="kn-auto-grid">
            <label>
              {t("evidence.type")}
              <select required value={form.evidence_type_id} disabled={save.isPending} onChange={(e) => setForm({ ...form, evidence_type_id: e.target.value })}>
                <option value="">{t("evidence.chooseType")}</option>
                {types.data?.map((type) => <option key={type.id} value={type.id}>{type.name_en || type.name_zh}</option>)}
              </select>
            </label>
            <div className="kn-stack-1">
              <input
                type="search"
                aria-label={t("evidence.searchControls")}
                placeholder={t("evidence.searchControlsPlaceholder")}
                value={controlQuery}
                disabled={save.isPending}
                onChange={(e) => setControlQuery(e.target.value)}
              />
              <label>
                {t("evidence.control")}
                <select
                  required
                  value={form.control_id}
                  disabled={save.isPending}
                  onChange={(e) => {
                    setForm({ ...form, control_id: e.target.value });
                    setChosenControl(controlOptions.find((control) => String(control.id) === e.target.value) ?? null);
                  }}
                >
                  <option value="">{t("evidence.chooseControl")}</option>
                  {controlOptions.map((control) => <option key={control.id} value={control.id}>{control.code} · {control.title}</option>)}
                </select>
              </label>
              {controls.error && <span role="alert" className="kn-text-2xs">⚠️ {controls.error.message}</span>}
              {controls.isSuccess && controlQuery.trim() && controls.data.length === 0 && (
                <span className="kn-muted kn-text-2xs">{t("evidence.noControlMatch")}</span>
              )}
              {controls.data?.length === CONTROL_SEARCH_LIMIT && (
                <span className="kn-muted kn-text-2xs">{t("evidence.controlMatchesTruncated", { count: CONTROL_SEARCH_LIMIT })}</span>
              )}
            </div>
            <label>
              {t("evidence.asset")}
              <select value={form.tech_asset_id} disabled={save.isPending} onChange={(e) => setForm({ ...form, tech_asset_id: e.target.value })}>
                <option value="">{t("evidence.noAsset")}</option>
                {assets.data?.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
              </select>
            </label>
            <label>
              {t("evidence.titleField")}
              <input required value={form.title} disabled={save.isPending} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </label>
            <label>
              {t("evidence.owner")}
              <input type="number" min="1" value={form.owner_user_id} disabled={save.isPending} onChange={(e) => setForm({ ...form, owner_user_id: e.target.value })} />
            </label>
            <label>
              {t("evidence.status")}
              <select value={form.status} disabled={save.isPending} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                {evidenceStatuses.map((value) => <option key={value} value={value}>{t(`evidence.statuses.${value}`)}</option>)}
              </select>
            </label>
            <label>
              {t("evidence.lastCollected")}
              <input type="datetime-local" value={form.last_collected_at} disabled={save.isPending} onChange={(e) => setForm({ ...form, last_collected_at: e.target.value })} />
            </label>
            <label>
              {t("evidence.validUntil")}
              <input type="datetime-local" value={form.valid_until} disabled={save.isPending} onChange={(e) => setForm({ ...form, valid_until: e.target.value })} />
            </label>
            <label>
              {t("evidence.location")}
              <input value={form.location_hint} disabled={save.isPending} onChange={(e) => setForm({ ...form, location_hint: e.target.value })} />
            </label>
            <label>
              {t("evidence.filePath")}
              <input value={form.file_path} disabled={save.isPending} onChange={(e) => setForm({ ...form, file_path: e.target.value })} />
            </label>
          </div>
          <PrerequisiteNotice
            items={[
              { satisfied: !anyControl.isSuccess || anyControl.data.length > 0, label: t("evidence.prereqControls"), to: "/controls" },
              { satisfied: (types.data?.length ?? 0) > 0, label: t("evidence.prereqTypes"), to: "/settings/evidence-types" },
            ]}
          />
          {error && <p role="alert">⚠️ {error}</p>}
          <div className="kn-row kn-mt-4">
            <button className="kn-btn-primary" type="submit" disabled={save.isPending || !form.title.trim() || !form.evidence_type_id || !form.control_id}>{t("common.save")}</button>
            <button type="button" className="kn-btn-secondary" disabled={save.isPending} onClick={() => { setForm(null); setEditing(null); }}>{t("common.cancel")}</button>
          </div>
        </form>
      )}

      {evidence.isPending && <LoadingState />}
      {evidence.error && <p role="alert">⚠️ {evidence.error.message}</p>}
      {evidence.data && evidence.data.length === 0 && !evidence.isPending && <Card><EmptyState /></Card>}
      {!!evidence.data?.length && (
        <DataTable
          head={
            <tr>
              <th>{t("evidence.titleField")}</th>
              <th>{t("evidence.type")}</th>
              <th>{t("evidence.control")}</th>
              <th>{t("evidence.owner")}</th>
              <th>{t("evidence.validUntil")}</th>
              <th>{t("evidence.status")}</th>
              {canWrite && <th />}
            </tr>
          }
        >
          {evidence.data.map((item) => {
            const isExpired = item.display_status === "expired";
            return (
              <tr key={item.id}>
                <td>
                  <strong>{item.title}</strong>
                  <div className="kn-muted kn-text-2xs">{item.location_hint || "—"}</div>
                </td>
                <td>{item.evidence_type_name ?? typeName(item.evidence_type_id)}</td>
                <td>
                  <code>{item.control_code ?? `#${item.control_id}`}</code>
                  {item.control_title && <div className="kn-muted kn-text-2xs">{item.control_title}</div>}
                </td>
                <td>{item.owner_user_id ? `#${item.owner_user_id}` : "—"}</td>
                <td className={isExpired ? "kn-status-expired" : undefined}>
                  {item.valid_until ? new Date(item.valid_until).toLocaleDateString() : "—"}
                </td>
                <td>
                  <span className={`kn-badge ${isExpired ? "kn-badge-ruby" : item.display_status === "valid" ? "kn-badge-emerald" : item.display_status === "missing" ? "kn-badge-amber" : "kn-badge-blue"}`}>
                    {t(`evidence.statuses.${item.display_status}`, { defaultValue: item.display_status })}
                  </span>
                </td>
                {canWrite && (
                  <td>
                    <button className="kn-btn-secondary kn-btn-sm" onClick={() => beginEdit(item)}>
                      {t("common.edit")}
                    </button>
                  </td>
                )}
              </tr>
            );
          })}
        </DataTable>
      )}
    </section>
  );
}
