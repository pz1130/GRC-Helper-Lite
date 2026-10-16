import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { request } from "../../api";
import { LoadingState } from "../../components/ui";

interface Settings {
  enabled: boolean;
  provider_config_id: number | null;
  provider_kinds: string[];
}
interface Provider { id: number; name: string; kind: string; enabled: boolean; model: string }

export function DecisionModel({ providers }: { providers: Provider[] }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["decision-settings"], queryFn: async () => {
    const result = await request<Settings>("/api/settings/decision-model");
    if (!Array.isArray(result?.provider_kinds)) throw new Error(t("decision.invalidResponse"));
    return result;
  } });
  const [enabled, setEnabled] = useState(false);
  const [providerId, setProviderId] = useState("");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (settings.data) {
      setEnabled(settings.data.enabled);
      setProviderId(settings.data.provider_config_id?.toString() ?? "");
    }
  }, [settings.data]);
  const save = useMutation({
    mutationFn: () => request("/api/settings/decision-model", {
      method: "PUT", body: JSON.stringify({ enabled, provider_config_id: providerId ? Number(providerId) : null }),
    }),
    onSuccess: () => {
      setSaved(true);
      void client.invalidateQueries({ queryKey: ["decision-settings"] });
      void client.invalidateQueries({ queryKey: ["requirement-scan"] });
      void client.invalidateQueries({ queryKey: ["routing"] });
    },
  });
  return <section className="kn-mb-6 kn-card" aria-label={t("decision.settingsTitle")}>
    <h3>{t("decision.settingsTitle")}</h3>
    <p>{t("decision.settingsHint")}</p>
    <p>{t("decision.dataNotice")}</p>
    <p>{t("decision.costNotice")}</p>
    {settings.isPending && <LoadingState />}
    {settings.error && <p role="alert">{settings.error.message}</p>}
    {settings.data && <form onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
      <label>{t("decision.provider")}
        <select value={providerId} disabled={save.isPending} onChange={(event) => { setProviderId(event.target.value); setSaved(false); }}>
          <option value="">{t("decision.unconfigured")}</option>
          {providers.filter((p) => settings.data.provider_kinds.includes(p.kind)).map((p) =>
            <option key={p.id} value={p.id} disabled={!p.enabled}>{p.name} · {p.model}{p.enabled ? "" : ` (${t("settings.providerDisabled")})`}</option>)}
        </select>
      </label>
      <label style={{ display: "flex", flexDirection: "row", gap: 8, margin: "12px 0" }}>
        <input type="checkbox" checked={enabled} disabled={save.isPending} onChange={(event) => { setEnabled(event.target.checked); setSaved(false); }} />
        {t("decision.enable")}
      </label>
      <button className="kn-btn-primary" disabled={save.isPending || (enabled && !providerId)}>{t("decision.save")}</button>
      {save.error && <p role="alert">{save.error.message}</p>}
      {saved && <p role="status">{t("decision.saved")}</p>}
    </form>}
  </section>;
}
