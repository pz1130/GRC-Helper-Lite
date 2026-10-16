import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, Fragment, useState } from "react";
import { useTranslation } from "react-i18next";

import { request } from "../api";
import { useAuth } from "../auth";
import { Card, DataTable, EmptyState, LoadingState, PageHeader } from "../components/ui";

interface TechAsset {
  id: number;
  name: string;
  category: string;
  vendor: string;
  environment: string;
  owner_user_id: number | null;
  scope_note: string;
  status: string;
  created_at: string;
}
interface SupportedControl {
  id: number;
  control_id: number;
  tech_asset_id: number | null;
  description: string;
  how_enforced: string;
  status: string;
  na_justification: string | null;
  owner_user_id: number | null;
  last_verified_at: string | null;
  control_code: string | null;
  control_title: string | null;
}

interface AssetForm {
  name: string;
  category: string;
  vendor: string;
  environment: string;
  owner_user_id: string;
  scope_note: string;
  status: string;
}

const emptyForm: AssetForm = {
  name: "",
  category: "other",
  vendor: "",
  environment: "prod",
  owner_user_id: "",
  scope_note: "",
  status: "active",
};

const categories = ["cloud", "pam", "siem", "edr", "iam", "dlp", "backup", "network", "database", "ticketing", "other"];
const environments = ["prod", "dr", "dev", "all"];
const assetStatuses = ["active", "planned", "retiring"];

function optionalId(value: string): number | null {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

export function TechAssets() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const client = useQueryClient();
  const canWrite = user?.role !== "viewer";
  const [form, setForm] = useState<AssetForm | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [error, setError] = useState("");

  const assets = useQuery({
    queryKey: ["tech-assets"],
    queryFn: () => request<TechAsset[]>("/api/tech-assets"),
  });
  const controls = useQuery({
    queryKey: ["tech-asset-controls", expandedId],
    queryFn: () => request<SupportedControl[]>(`/api/tech-assets/${expandedId}/controls`),
    enabled: expandedId !== null,
  });
  const save = useMutation({
    mutationFn: () => {
      if (!form) throw new Error(t("techAssets.formRequired"));
      const payload = {
        ...form,
        owner_user_id: optionalId(form.owner_user_id),
      };
      return request<TechAsset>(
        editingId === null ? "/api/tech-assets" : `/api/tech-assets/${editingId}`,
        { method: editingId === null ? "POST" : "PATCH", body: JSON.stringify(payload) },
      );
    },
    onMutate: () => setError(""),
    onError: (caught: Error) => setError(caught.message),
    onSuccess: async () => {
      setForm(null);
      setEditingId(null);
      await client.invalidateQueries({ queryKey: ["tech-assets"] });
    },
  });

  function beginCreate() {
    setError("");
    save.reset();
    setEditingId(null);
    setForm(emptyForm);
  }

  function beginEdit(asset: TechAsset) {
    setError("");
    save.reset();
    setEditingId(asset.id);
    setForm({
      name: asset.name,
      category: asset.category,
      vendor: asset.vendor,
      environment: asset.environment,
      owner_user_id: asset.owner_user_id ? String(asset.owner_user_id) : "",
      scope_note: asset.scope_note,
      status: asset.status,
    });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (form?.name.trim()) save.mutate();
  }

  return (
    <section>
      <PageHeader
        title={t("techAssets.title")} subtitle={t("techAssets.subtitle")}
        actions={canWrite && (
        <button className="kn-btn-primary kn-btn-sm" onClick={beginCreate}>
          {t("common.add")}
        </button>
      )}
      />

      {assets.isPending && <LoadingState />}
      {assets.error && <p role="alert">⚠️ {assets.error.message}</p>}

      {form && canWrite && (
        <form className="kn-card kn-form-card kn-form-card--narrow" onSubmit={submit}>
          <h3 className="kn-mb-4">{t(editingId === null ? "techAssets.add" : "techAssets.edit")}</h3>
          <div className="kn-auto-grid">
            <label>
              {t("techAssets.name")}
              <input required value={form.name} disabled={save.isPending} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label>
              {t("techAssets.category")}
              <select value={form.category} disabled={save.isPending} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                {categories.map((option) => <option key={option} value={option}>{t(`techAssets.categories.${option}`)}</option>)}
              </select>
            </label>
            <label>
              {t("techAssets.vendor")}
              <input value={form.vendor} disabled={save.isPending} onChange={(e) => setForm({ ...form, vendor: e.target.value })} />
            </label>
            <label>
              {t("techAssets.environment")}
              <select value={form.environment} disabled={save.isPending} onChange={(e) => setForm({ ...form, environment: e.target.value })}>
                {environments.map((option) => <option key={option} value={option}>{t(`techAssets.environments.${option}`)}</option>)}
              </select>
            </label>
            <label>
              {t("techAssets.status")}
              <select value={form.status} disabled={save.isPending} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                {assetStatuses.map((option) => <option key={option} value={option}>{t(`techAssets.statuses.${option}`)}</option>)}
              </select>
            </label>
            <label>
              {t("techAssets.owner")}
              <input type="number" min="1" value={form.owner_user_id} disabled={save.isPending} onChange={(e) => setForm({ ...form, owner_user_id: e.target.value })} />
            </label>
            <label className="kn-col-span-all">
              {t("techAssets.scope")}
              <textarea rows={3} value={form.scope_note} disabled={save.isPending} onChange={(e) => setForm({ ...form, scope_note: e.target.value })} />
            </label>
          </div>
          {error && <p role="alert">⚠️ {error}</p>}
          <div className="kn-row kn-mt-4">
            <button className="kn-btn-primary" type="submit" disabled={save.isPending || !form.name.trim()}>{t("common.save")}</button>
            <button type="button" className="kn-btn-secondary" disabled={save.isPending} onClick={() => { setForm(null); setEditingId(null); }}>{t("common.cancel")}</button>
          </div>
        </form>
      )}

      {assets.data && assets.data.length === 0 && !assets.isPending && (
        <Card><EmptyState /></Card>
      )}
      {!!assets.data?.length && (
        <DataTable
          head={
            <>
          <tr>
            <th>{t("techAssets.name")}</th>
            <th>{t("techAssets.category")}</th>
            <th>{t("techAssets.vendor")}</th>
            <th>{t("techAssets.environment")}</th>
            <th>{t("techAssets.status")}</th>
            <th>{t("techAssets.controls")}</th>
            {canWrite && <th />}
          </tr>
            </>
          }
        >
          {assets.data.map((asset) => (
            <Fragment key={asset.id}>
              <tr>
                <td>
                  <strong>{asset.name}</strong>
                  {asset.scope_note && <div className="kn-muted kn-text-2xs">{asset.scope_note}</div>}
                </td>
                <td><span className="kn-badge kn-badge-blue">{t(`techAssets.categories.${asset.category}`)}</span></td>
                <td>{asset.vendor || "—"}</td>
                <td>{t(`techAssets.environments.${asset.environment}`)}</td>
                <td><span className={`kn-badge ${asset.status === "active" ? "kn-badge-emerald" : asset.status === "retiring" ? "kn-badge-ruby" : "kn-badge-amber"}`}>{t(`techAssets.statuses.${asset.status}`)}</span></td>
                <td>
                  <button className="kn-btn-secondary kn-btn-sm" onClick={() => setExpandedId(expandedId === asset.id ? null : asset.id)}>
                    {expandedId === asset.id ? t("techAssets.hideControls") : t("techAssets.showControls")}
                  </button>
                </td>
                {canWrite && <td><button className="kn-btn-secondary kn-btn-sm" onClick={() => beginEdit(asset)}>{t("common.edit")}</button></td>}
              </tr>
              {expandedId === asset.id && (
                <tr key={`${asset.id}-controls`}>
                  <td colSpan={canWrite ? 7 : 6} className="kn-card--subtle">
                    <strong>{t("techAssets.supportedControls")}</strong>
                    {controls.isPending && <LoadingState />}
                    {controls.error && <p role="alert">⚠️ {controls.error.message}</p>}
                    {controls.data?.length === 0 && <p className="kn-muted">{t("techAssets.noControls")}</p>}
                    {!!controls.data?.length && <ul className="kn-list-indented">
                      {controls.data.map((control) => <li key={control.id}><code>{control.control_code ?? `#${control.control_id}`}</code> {control.control_title ?? ""} · <span className="kn-badge">{t(`techAssets.implementationStatuses.${control.status}`)}</span></li>)}
                    </ul>}
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </DataTable>
      )}
    </section>
  );
}
