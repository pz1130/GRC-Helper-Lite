import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { request } from "../../api";
import { DecisionModel } from "./DecisionModel";
import { hasText } from "../../i18n";
import { Card, DataTable } from "../../components/ui";
import { HoldButton } from "../../components/fx";

interface Provider {
  id: number;
  name: string;
  kind: string;
  model: string;
  base_url: string | null;
  enabled: boolean;
  is_fallback: boolean;
  api_key_masked: string;
  capabilities?: string[];
}

interface Routing {
  id: number;
  task_key: string;
  provider_config_id: number;
  temperature: number;
  max_tokens: number;
}

// 探测结果：后端返 code + 数字，句子由前端按语言组装。
interface ProbeResult {
  ok: boolean;
  capability: "chat" | "embedding" | "decision" | null;
  code: "decision_ok" | "chat_ok" | "embedding_ok" | "embedding_dimension_mismatch" | "unreachable";
  dimension?: number;
  expected_dimension?: number;
  failures?: Record<string, string>;
}

const KINDS = [
  "jev",
  "anthropic",
  "openai",
  "azure_openai",
  "gemini",
  "deepseek",
  "qwen",
  "ollama",
  "minimax",
  "openai_compatible",
];

// 任务清单由后端给（app/llm/tasks.py）。这里原先硬编码了一份，注释写
// "八个推理任务 + embedding"＝9 而实际列了 11 个——两边没有约束关系就会漂。
interface TaskSpec {
  key: string;
  capability: "chat" | "embedding" | "decision";
  implemented: boolean;
}

export function Providers() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState<number | null>(null);
  const [form, setForm] = useState({
    name: "",
    kind: "anthropic",
    model: "claude-opus-5",
    api_key: "",
    base_url: "",
  });
  const [probe, setProbe] = useState<Record<number, string>>({});

  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: () => request<Provider[]>("/api/settings/providers"),
  });
  const routing = useQuery({
    queryKey: ["routing"],
    queryFn: () => request<Routing[]>("/api/settings/routing"),
  });

  const create = useMutation({
    mutationFn: () =>
      request<Provider>("/api/settings/providers", {
        method: "POST",
        body: JSON.stringify({ ...form, base_url: form.base_url || null }),
      }),
    onSuccess: () => {
      setForm({ ...form, name: "", api_key: "" });
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });

  const tasks = useQuery({
    queryKey: ["routing-tasks"],
    queryFn: () => request<TaskSpec[]>("/api/settings/routing/tasks"),
  });

  // 一键应用只绑同一 capability 下已实现的任务。**embedding 单列一行**：
  // 它要向量模型，被聊天模型顺手绑走不会报错，只会让检索静默退化成纯关键字。
  const bulkRoute = useMutation({
    mutationFn: (body: { provider_config_id: number; capability: "chat" | "embedding" }) =>
      request("/api/settings/routing/bulk", {
        method: "PUT",
        body: JSON.stringify({ ...body, temperature: 0, max_tokens: 4096 }),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["routing"] }),
  });

  const route = useMutation({
    mutationFn: (body: { task_key: string; provider_config_id: number }) =>
      request<Routing>("/api/settings/routing", {
        method: "PUT",
        body: JSON.stringify({ ...body, temperature: 0, max_tokens: 4096 }),
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["routing"] }),
  });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      request(`/api/settings/providers/${id}`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["providers"] }),
  });
  // 被任务路由指着的会被后端拒掉（409），把那句话原样显示出来——
  // 它写着还有哪几个任务在用它。
  const remove = useMutation({
    mutationFn: (id: number) => request(`/api/settings/providers/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setConfirming(null);
      void queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });

  async function testConnection(id: number) {
    // 一律函数式更新：探测要几秒，几行可以同时在测。拿点击那一刻的 probe 快照
    // 去展开，先回来的结果会被后回来的那一行抹掉。
    const show = (text: string) => setProbe((current) => ({ ...current, [id]: text }));
    show(t("common.loading"));
    let result: ProbeResult;
    try {
      result = await request<ProbeResult>(`/api/settings/providers/${id}/test`, {
        method: "POST",
      });
    } catch (caught) {
      // 调用方是 `void testConnection(...)`：不接住，失败就是「点了没反应」。
      show(`❌ ${(caught as Error).message}`);
      return;
    }
    // 后端只给 code 和数字，句子在这里组——它曾经给整句中文，界面切英文后
    // 这一列照样冒中文出来（后端 llm/router.py）。
    // 用 hasText 而不是 defaultValue：i18n.ts 的 parseMissingKeyHandler 优先级
    // 更高，defaultValue 在这个仓库里永远不会生效。
    const key = `settings.providersConfig.probe.${result.code}`;
    const text = hasText(key)
      ? t(key, { dimension: result.dimension, expected: result.expected_dimension })
      : t("settings.providersConfig.probe.unreachable");
    // 上游的报错原文不翻译：那是外部系统说的话，改写它等于篡改证据。
    const upstream = Object.entries(result.failures ?? {})
      .map(([capability, error]) => `${capability}: ${error}`)
      .join("; ");
    const suffix = upstream ? ` — ${upstream}` : "";
    show((result.ok ? "✅ " : "❌ ") + text + suffix);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <div>
      <Card className="kn-mb-6">
        <h3 className="kn-section-title">{t("settings.providers")}</h3>
        <DataTable className="kn-m-0" head={<><tr>
                <th>{t("settings.providersConfig.name")}</th>
                <th>{t("settings.providersConfig.kind")}</th>
                <th>{t("settings.providersConfig.model")}</th>
                <th>{t("settings.providersConfig.apiKey")}</th>
                <th className="kn-text-right">{t("common.actions")}</th>
              </tr></>}>
              {providers.data?.map((p) => (
                <tr key={p.id}>
                  <td>
                    <span className="kn-strong kn-primary-text">{p.name}</span>
                    {p.is_fallback && <span className="kn-ml-2 kn-badge kn-badge-purple">{t("settings.providersConfig.fallback")}</span>}
                  </td>
                  <td>
                    <span className="kn-badge">{p.kind}</span>
                  </td>
                  <td>
                    <span className="kn-secondary kn-text-sm">{p.model}</span>
                  </td>
                  <td>
                    <code>{p.api_key_masked}</code>
                  </td>
                  <td className="kn-text-right">
                    <div style={{ display: "inline-flex", alignItems: "center", gap: 8, justifyContent: "flex-end" }}>
                      <button className="kn-btn-secondary kn-btn-sm" onClick={() => void testConnection(p.id)}>
                        {t("common.test")}
                      </button>
                      {/* 一键应用按 capability 分成两个，不是一个。把聊天模型绑到
                          embedding 上不会报错，只会让向量静默不生成、检索退化成
                          纯关键字——那是最难被发现的一类配置错误。 */}
                      <button
                        className="kn-btn-secondary kn-btn-sm"
                        disabled={bulkRoute.isPending || p.capabilities?.includes("decision")}
                        title={t("settings.providersConfig.applyChatHint")}
                        onClick={() => bulkRoute.mutate({ provider_config_id: p.id, capability: "chat" })}
                      >
                        {t("settings.providersConfig.applyChat")}
                      </button>
                      <button
                        className="kn-btn-secondary kn-btn-sm"
                        disabled={bulkRoute.isPending || p.capabilities?.includes("decision")}
                        title={t("settings.providersConfig.applyEmbeddingHint")}
                        onClick={() => bulkRoute.mutate({ provider_config_id: p.id, capability: "embedding" })}
                      >
                        {t("settings.providersConfig.applyEmbedding")}
                      </button>
                      <button
                        className="kn-btn-secondary kn-btn-sm"
                        disabled={toggle.isPending}
                        onClick={() => toggle.mutate({ id: p.id, enabled: !p.enabled })}
                      >
                        {t(p.enabled ? "settings.providerDisable" : "settings.providerEnable")}
                      </button>
                      {confirming === p.id ? (
                        <>
                          <HoldButton
                            className="kn-btn-danger kn-btn-sm"
                            disabled={remove.isPending}
                            onConfirm={() => remove.mutate(p.id)}
                          >
                            {t("settings.providerConfirmDelete")}
                          </HoldButton>
                          <button className="kn-btn-secondary kn-btn-sm" onClick={() => { setConfirming(null); remove.reset(); }}>
                            {t("common.cancel")}
                          </button>
                        </>
                      ) : (
                        <button
                          className="kn-btn-danger kn-btn-sm"
                          onClick={() => { setConfirming(p.id); remove.reset(); }}
                        >
                          {t("common.delete")}
                        </button>
                      )}
                      {!p.enabled && <span className="kn-badge">{t("settings.providerDisabled")}</span>}
                      {probe[p.id] && (
                        <span className="kn-text-xs">{probe[p.id]}</span>
                      )}
                      {confirming === p.id && remove.error && (
                        <span role="alert" style={{ fontSize: "0.8125rem", color: "var(--accent-red)" }}>
                          ⚠️ {remove.error.message}
                        </span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </DataTable>
      </Card>

      {/* Add Provider Card */}
      <Card className="kn-mb-6">
        <h4 className="kn-m-0 kn-mb-3 kn-text-md">{t("common.add")}</h4>
        <form onSubmit={submit} className="kn-stack-3 kn-max-w-md">
          <input
            placeholder={t("settings.providersConfig.name")}
            value={form.name}
            required
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value, model: e.target.value === "jev" ? "jev-1.13.0" : form.model })}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          <input
            placeholder={t("settings.providersConfig.model")}
            value={form.model}
            required
            onChange={(e) => setForm({ ...form, model: e.target.value })}
          />
          <input
            placeholder={t("settings.providersConfig.baseUrlPlaceholder")}
            value={form.base_url}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
          />
          <input
            type="password"
            placeholder={t("settings.providersConfig.apiKey")}
            value={form.api_key}
            required
            onChange={(e) => setForm({ ...form, api_key: e.target.value })}
          />
          <div>
            <button type="submit" className="kn-btn-wide kn-btn-primary">
              {t("common.save")}
            </button>
          </div>
        </form>
      </Card>

      {create.error && <p role="alert">{create.error.message}</p>}
      <DecisionModel providers={providers.data ?? []} />

      {/* Routing Matrix Card */}
      <Card>
        <h3 className="kn-section-title">{t("settings.routing")}</h3>
        <p className="kn-card-note">{t("settings.providersConfig.routingHint")}</p>
        <DataTable className="kn-m-0" head={<><tr>
                <th>{t("settings.providersConfig.taskPipeline")}</th>
                <th>{t("settings.providersConfig.assignedProvider")}</th>
              </tr></>}>
              {tasks.data?.filter((task) => task.key !== "requirement_decision").map((task) => {
                const current = routing.data?.find((r) => r.task_key === task.key);
                return (
                  <tr key={task.key}>
                    <td>
                      <span title={task.key}>{t(`taskNames.${task.key}`, { defaultValue: task.key })}</span>
                      {task.capability === "embedding" && (
                        <span className="kn-task-tag">{t("settings.providersConfig.needsVectorModel")}</span>
                      )}
                      {!task.implemented && (
                        <span className="kn-task-tag kn-task-tag--muted">
                          {t("settings.providersConfig.notImplemented")}
                        </span>
                      )}
                    </td>
                    <td>
                      <select
                        aria-label={t("settings.providersConfig.routeFor", { task: t(`taskNames.${task.key}`, { defaultValue: task.key }) })}
                        value={current?.provider_config_id ?? ""}
                        disabled={!task.implemented}
                        title={task.implemented ? undefined : t("settings.providersConfig.notImplementedHint")}
                        onChange={(e) =>
                          route.mutate({ task_key: task.key, provider_config_id: Number(e.target.value) })
                        }
                        style={{ minWidth: 260, padding: "5px 28px 5px 12px" }}
                      >
                        <option value="">—</option>
                        {providers.data?.filter((p) => !p.capabilities?.includes("decision")).map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name} · {p.model}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                );
              })}
            </DataTable>
      </Card>
    </div>
  );
}
