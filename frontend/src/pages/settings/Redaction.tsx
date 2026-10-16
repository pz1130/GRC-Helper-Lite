import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { request } from "../../api";
import { Card, DataTable } from "../../components/ui";

type Ruleset = "generation" | "embedding";

interface Rule {
  id: number;
  ruleset: Ruleset;
  pattern_type: "regex" | "dictionary";
  pattern: string;
  replacement_prefix: string;
  enabled: boolean;
  order_index: number;
  note: string | null;
}

export function Redaction() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [ruleset, setRuleset] = useState<Ruleset>("generation");
  const [sample, setSample] = useState("");
  const [form, setForm] = useState({
    pattern_type: "regex" as "regex" | "dictionary",
    pattern: "",
    replacement_prefix: "",
    note: "",
  });

  const rules = useQuery({
    queryKey: ["redaction"],
    queryFn: () => request<Rule[]>("/api/settings/redaction"),
  });

  const create = useMutation({
    mutationFn: () =>
      request<Rule>("/api/settings/redaction", {
        method: "POST",
        body: JSON.stringify({ ...form, ruleset, enabled: true, order_index: 100 }),
      }),
    onSuccess: () => {
      setForm({ ...form, pattern: "", replacement_prefix: "", note: "" });
      void queryClient.invalidateQueries({ queryKey: ["redaction"] });
    },
  });

  // 预览结果只对应发起时的样例与规则集：改了任一项就作废，免得旧的「已脱敏」
  // 结果留在屏幕上，被当成新输入的结果。失败要说出来，不能沉默。
  const preview = useMutation({
    mutationFn: () =>
      request<{ redacted: string; hits: Record<string, number> }>("/api/settings/redaction/preview", {
        method: "POST",
        body: JSON.stringify({ ruleset, text: sample }),
      }),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <div>
      {/* Ruleset selector & Table Card */}
      <Card className="kn-mb-6">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12, marginBottom: 14 }}>
          <div>
            <h3 style={{ margin: 0, fontSize: "1.125rem" }}>{t("settings.redaction")}</h3>
            <p style={{ color: "var(--text-secondary)", fontSize: "0.8125rem", margin: "4px 0 0 0" }}>
              {t("settings.redactionConfig.subtitle")}
            </p>
          </div>
          <label className="kn-inline-label kn-m-0">
            <span>{t("settings.redactionConfig.ruleset")}</span>
            <select
              value={ruleset}
              onChange={(e) => { setRuleset(e.target.value as Ruleset); preview.reset(); }}
              style={{ padding: "5px 28px 5px 12px" }}
            >
              <option value="generation">{t("settings.redactionConfig.generation")}</option>
              <option value="embedding">{t("settings.redactionConfig.embedding")}</option>
            </select>
          </label>
        </div>

        {/* 脱敏决定哪些内容在发给模型前被遮住：规则没加载出来时，空表不能看起来像「没有规则」 */}
        {rules.error && <p role="alert">⚠️ {rules.error.message}</p>}
        <DataTable className="kn-m-0" head={<><tr>
                <th>{t("settings.redactionConfig.type")}</th>
                <th>{t("settings.redactionConfig.pattern")}</th>
                <th>{t("settings.redactionConfig.prefix")}</th>
                <th>{t("settings.redactionConfig.enabled")}</th>
              </tr></>}>
              {rules.data
                ?.filter((r) => r.ruleset === ruleset)
                .map((r) => (
                  <tr key={r.id}>
                    <td>
                      <span className="kn-badge">{r.pattern_type}</span>
                    </td>
                    <td>
                      <code>{r.pattern}</code>
                    </td>
                    <td>
                      <span className="kn-badge kn-badge-blue">{r.replacement_prefix}</span>
                    </td>
                    <td>
                      {r.enabled ? (
                        <span className="kn-badge kn-badge-emerald">
                          <span className="kn-dot kn-dot-emerald" /> {t("settings.redactionConfig.active")}
                        </span>
                      ) : (
                        <span className="kn-badge">—</span>
                      )}
                    </td>
                  </tr>
                ))}
            </DataTable>
      </Card>

      {/* Add Rule Card */}
      <Card className="kn-mb-6">
        <h4 className="kn-m-0 kn-mb-3 kn-text-md">{t("common.add")}</h4>
        <form onSubmit={submit} className="kn-stack-3 kn-max-w-md">
          <select
            aria-label={t("settings.redactionConfig.type")}
            value={form.pattern_type}
            onChange={(e) =>
              setForm({ ...form, pattern_type: e.target.value as "regex" | "dictionary" })
            }
          >
            <option value="regex">{t("settings.redactionConfig.regex")}</option>
            <option value="dictionary">{t("settings.redactionConfig.dictionaryOption")}</option>
          </select>
          <textarea
            placeholder={t("settings.redactionConfig.pattern")}
            value={form.pattern}
            required
            rows={3}
            onChange={(e) => setForm({ ...form, pattern: e.target.value })}
          />
          <input
            placeholder={t("settings.redactionConfig.prefixPlaceholder")}
            value={form.replacement_prefix}
            required
            onChange={(e) => setForm({ ...form, replacement_prefix: e.target.value })}
          />
          <input
            placeholder={t("settings.redactionConfig.notePlaceholder")}
            value={form.note}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
          />
          {create.error && <p role="alert">⚠️ {create.error.message}</p>}
          <div>
            <button type="submit" className="kn-btn-wide kn-btn-primary" disabled={create.isPending}>
              {t("common.save")}
            </button>
          </div>
        </form>
      </Card>

      {/* Live Preview Sandbox Card */}
      <Card>
        <h4 className="kn-m-0 kn-mb-1 kn-text-md">{t("settings.redactionConfig.sendPreview")}</h4>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.8125rem", marginBottom: 14 }}>
          {t("settings.redactionConfig.sendPreviewHint")}
        </p>
        <textarea
          aria-label={t("settings.redactionConfig.previewInputAria")}
          rows={4}
          style={{ width: "100%", maxWidth: 720, marginBottom: 12 }}
          value={sample}
          onChange={(e) => { setSample(e.target.value); preview.reset(); }}
        />
        <div>
          <button className="kn-btn-primary kn-btn-sm" disabled={preview.isPending} onClick={() => preview.mutate()}>
            {t("settings.redactionConfig.previewBtn")}
          </button>
        </div>
        {preview.error && <p role="alert">⚠️ {preview.error.message}</p>}
        {preview.data && (
          <pre
            style={{
              marginTop: 14,
              padding: 16,
              whiteSpace: "pre-wrap",
              background: "var(--pre-bg)",
              border: "1px solid var(--stage-border)",
              borderRadius: "var(--radius-sm)",
              fontSize: "0.875rem",
              lineHeight: 1.6,
              color: "var(--pre-color)",
            }}
          >
            {preview.data.redacted}
            {"\n\n" + t("settings.redactionConfig.hits") + ": " + JSON.stringify(preview.data.hits)}
          </pre>
        )}
      </Card>
    </div>
  );
}
