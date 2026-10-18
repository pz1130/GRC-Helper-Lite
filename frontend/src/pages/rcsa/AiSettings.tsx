// RCSA 两个 AI 通道的设置（仅管理员）。每个通道单独绑定服务、登记批准依据、选外发输入，默认关闭。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { request } from "../../api";
import { useAuth } from "../../auth";
import { Card, EmptyState, LoadingState, PageHeader } from "../../components/ui";
import { api, send, type AiChannel, type InputType } from "./api";
import { ErrorNotice, RcsaGate } from "./shared";

interface Provider {
  id: number;
  name: string;
  kind: string;
  model: string;
  enabled: boolean;
}

type Channel = "prefill" | "evidence_relevance";

function ChannelForm({ channel, value, providers }: { channel: Channel; value: AiChannel; providers: Provider[] }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [enabled, setEnabled] = useState(value.enabled);
  const [approval, setApproval] = useState(value.approval_ref);
  const [inputs, setInputs] = useState<InputType[]>(value.allowed_input_types);
  const [provider, setProvider] = useState(value.provider ? String(value.provider.id) : "");
  const save = useMutation({
    mutationFn: () => send(`/api/rcsa/ai/${channel}`, "PUT", {
      enabled, approval_ref: approval, allowed_input_types: inputs,
      provider_config_id: provider ? Number(provider) : null,
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["rcsa", "ai"] }),
  });
  // 预填要聊天模型，证据相关性要判断模型（Jev）；前端只做预选，后端会再检查。
  const fits = (p: Provider) => (channel === "evidence_relevance" ? p.kind === "jev" : p.kind !== "jev");
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(); };
  return (
    <Card title={t(`rcsa.ai.channels.${channel}`)}>
      <form className="rcsa-form" onSubmit={submit} aria-label={t(`rcsa.ai.channels.${channel}`)}>
        <label className="kn-check-label rcsa-wide">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          {t("rcsa.ai.enabled")}
        </label>
        <label>{t("rcsa.ai.service")}
          <select value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="">{t("rcsa.ai.noService")}</option>
            {providers.filter(fits).map((p) => <option key={p.id} value={p.id}>{p.name} ({p.model})</option>)}
          </select>
        </label>
        <label>{t("rcsa.ai.approvalRef")}
          <input value={approval} onChange={(e) => setApproval(e.target.value)} placeholder={t("rcsa.ai.approvalHint")} />
        </label>
        <fieldset className="rcsa-wide">
          <legend>{t("rcsa.ai.allowedInputs")}</legend>
          {value.available_input_types.map((type) => (
            <label key={type} className="kn-check-label">
              <input type="checkbox" checked={inputs.includes(type)}
                onChange={(e) => setInputs(e.target.checked ? [...inputs, type] : inputs.filter((i) => i !== type))} />
              {t(`rcsa.ai.inputs.${type}`)}
            </label>
          ))}
        </fieldset>
        <div className="rcsa-actions rcsa-wide">
          <button type="submit" className="kn-btn kn-btn-primary" disabled={save.isPending}>{t("common.save")}</button>
          {save.isSuccess && <span role="status" className="kn-ok-text">{t("common.saved")}</span>}
        </div>
        <div className="rcsa-wide"><ErrorNotice error={save.error} /></div>
      </form>
    </Card>
  );
}

export function AiSettings() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const status = useQuery({ queryKey: ["rcsa", "ai"], queryFn: api.ai });
  const providers = useQuery({
    queryKey: ["settings", "providers"],
    queryFn: () => request<Provider[]>("/api/settings/providers"),
    enabled: user?.role === "admin",
  });
  if (user?.role !== "admin") {
    return <section className="kn-page"><EmptyState title={t("rcsa.ai.adminOnly")} /></section>;
  }
  return (
    <section className="kn-page">
      <PageHeader title={t("rcsa.ai.settingsTitle")} subtitle={t("rcsa.ai.settingsSubtitle")} />
      <RcsaGate>
        <p className="kn-secondary kn-text-xs">{t("rcsa.ai.settingsHint")}</p>
        {(status.isPending || providers.isPending) && <LoadingState />}
        <ErrorNotice error={status.error ?? providers.error} />
        {status.data && providers.data && (
          <div className="kn-stack-3">
            <ChannelForm key="prefill" channel="prefill" value={status.data.prefill} providers={providers.data} />
            <ChannelForm key="evidence_relevance" channel="evidence_relevance" value={status.data.evidence_relevance} providers={providers.data} />
          </div>
        )}
      </RcsaGate>
    </section>
  );
}
