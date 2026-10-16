import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { request } from "../../api";

interface Thresholds {
  auto_accept_threshold: number;
  force_manual_threshold: number;
  review_sample_rate: number;
  monthly_budget_usd: number;
}

export function ThresholdsPage() {
  const { t } = useTranslation();
  const [form, setForm] = useState<Thresholds>({
    auto_accept_threshold: 0.9,
    force_manual_threshold: 0.6,
    review_sample_rate: 0.1,
    monthly_budget_usd: 200,
  });
  const [error, setError] = useState("");

  const query = useQuery({
    queryKey: ["thresholds"],
    queryFn: () => request<Thresholds>("/api/settings/thresholds"),
  });

  // 只在第一次拿到服务端值时灌进表单。原先是 query.data 一变就整个覆盖，于是
  // 请求落在"用户已经改了几个框"与"点保存"之间时，输入被静默还原成服务端的值
  // ——用户以为自己改了，实际保存的是旧值。
  // 只灌一次还不够：**第一次**到达照样会盖掉加载期间的输入（e2e 的"阈值顺序
  // 错误会被拒绝"因此仍间歇性失败，快照里第一个框被还原、第二个框留着）。
  // 所以数据到达之前表单不可编辑；加载失败也不放开——拿默认值保存会覆盖
  // 服务端的真实配置。
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (query.data && !ready) {
      setForm(query.data);
      setReady(true);
    }
  }, [query.data, ready]);

  const save = useMutation({
    mutationFn: () =>
      request<Thresholds>("/api/settings/thresholds", {
        method: "PUT",
        body: JSON.stringify(form),
      }),
    onError: (e: Error) => setError(e.message),
    onSuccess: () => setError(""),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (form.auto_accept_threshold <= form.force_manual_threshold) {
      setError(t("settings.thresholdsConfig.orderError"));
      return;
    }
    save.mutate();
  }

  return (
    <div className="kn-card" style={{ maxWidth: 540 }}>
      <h3 style={{ margin: "0 0 8px 0", fontSize: "1.125rem" }}>{t("settings.thresholds")}</h3>
      <p style={{ color: "var(--text-secondary)", fontSize: "0.8125rem", marginBottom: 20 }}>
        {t("settings.thresholdsConfig.subtitle")}
      </p>

      {query.error && (
        <p role="alert">
          <span>⚠️</span> {query.error.message}
        </p>
      )}

      <form onSubmit={submit}>
        <fieldset
          disabled={!ready}
          style={{ display: "grid", gap: 16, border: 0, padding: 0, margin: 0, minWidth: 0 }}
        >
          <label>
            {t("settings.thresholdsConfig.autoAccept")}
            <input
              type="number"
              step="0.01"
              min="0"
              max="1"
              value={form.auto_accept_threshold}
              onChange={(e) => setForm({ ...form, auto_accept_threshold: Number(e.target.value) })}
            />
          </label>

          <label>
            {t("settings.thresholdsConfig.forceManual")}
            <input
              type="number"
              step="0.01"
              min="0"
              max="1"
              value={form.force_manual_threshold}
              onChange={(e) => setForm({ ...form, force_manual_threshold: Number(e.target.value) })}
            />
          </label>

          <label>
            {t("settings.thresholdsConfig.sampleRate")}
            <input
              type="number"
              step="0.01"
              min="0"
              max="1"
              value={form.review_sample_rate}
              onChange={(e) => setForm({ ...form, review_sample_rate: Number(e.target.value) })}
            />
          </label>

          <label>
            {t("settings.thresholdsConfig.monthlyBudget")}
            <input
              type="number"
              step="1"
              min="0"
              value={form.monthly_budget_usd}
              onChange={(e) => setForm({ ...form, monthly_budget_usd: Number(e.target.value) })}
            />
          </label>

          {error && (
            <p role="alert">
              <span>⚠️</span> {error}
            </p>
          )}

          <div>
            <button type="submit" className="kn-btn-wide kn-btn-primary">
              {t("common.save")}
            </button>
          </div>
        </fieldset>
      </form>
    </div>
  );
}
