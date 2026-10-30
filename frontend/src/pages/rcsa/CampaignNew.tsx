// 新建活动向导的前两步：期间，框架与评分版本。建好草稿后进入活动页继续选范围、分派和激活前检查。
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { request } from "../../api";
import { Card, PageHeader } from "../../components/ui";
import { api, send, type Campaign } from "./api";
import { ErrorNotice, RcsaGate, useUnsavedGuard } from "./shared";

interface Framework {
  id: number;
  key: string;
  name_en: string;
  version: string;
}

const STEPS = ["period", "basis", "scope", "assign", "check"] as const;

export function StepIndicator({ current }: { current: (typeof STEPS)[number] }) {
  const { t } = useTranslation();
  return (
    <ol className="kn-row kn-row-gap rcsa-steps" aria-label={t("rcsa.wizard.steps")}>
      {STEPS.map((step, index) => (
        <li key={step} aria-current={step === current ? "step" : undefined} className={step === current ? "kn-primary-text" : "kn-secondary"}>
          {index + 1}. {t(`rcsa.wizard.${step}`)}
        </li>
      ))}
    </ol>
  );
}

export function CampaignNew() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [step, setStep] = useState<"period" | "basis">("period");
  const [name, setName] = useState("");
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [timezone, setTimezone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [frameworkId, setFrameworkId] = useState("");
  const [profileId, setProfileId] = useState("");
  const frameworks = useQuery({ queryKey: ["frameworks"], queryFn: () => request<Framework[]>("/api/frameworks") });
  const profiles = useQuery({ queryKey: ["rcsa", "profiles"], queryFn: api.profiles });
  const dirty = Boolean(name || periodStart || periodEnd || dueAt);
  useUnsavedGuard(dirty);
  const create = useMutation({
    mutationFn: () =>
      send<Campaign>("/api/rcsa/campaigns", "POST", {
        name, period_start: periodStart, period_end: periodEnd, due_at: dueAt, timezone,
        framework_id: Number(frameworkId), profile_id: Number(profileId),
      }),
    onSuccess: (campaign) => navigate(`/rcsa/campaigns/${campaign.id}`),
  });
  const periodOk = name.trim() && periodStart && periodEnd && dueAt && periodStart <= periodEnd;

  const next = (event: FormEvent) => {
    event.preventDefault();
    if (periodOk) setStep("basis");
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate();
  };

  return (
    <section className="kn-page">
      <PageHeader title={t("rcsa.wizard.title")} subtitle={t("rcsa.wizard.subtitle")} />
      <RcsaGate>
        <StepIndicator current={step} />
        {step === "period" ? (
          <Card>
            <form className="rcsa-form" onSubmit={next}>
              <label className="rcsa-wide">{t("rcsa.campaigns.name")}
                <input required value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label>{t("rcsa.wizard.periodStart")}
                <input type="date" required value={periodStart} onChange={(e) => setPeriodStart(e.target.value)} />
              </label>
              <label>{t("rcsa.wizard.periodEnd")}
                <input type="date" required value={periodEnd} min={periodStart} onChange={(e) => setPeriodEnd(e.target.value)} />
              </label>
              <label>{t("rcsa.wizard.dueAt")}
                <input type="date" required value={dueAt} onChange={(e) => setDueAt(e.target.value)} />
              </label>
              <label>{t("rcsa.wizard.timezone")}
                <input required value={timezone} onChange={(e) => setTimezone(e.target.value)} />
              </label>
              {periodStart && periodEnd && periodStart > periodEnd ? (
                <p role="alert" className="kn-danger-text rcsa-wide">{t("errors.rcsa.period_order")}</p>
              ) : null}
              <div className="rcsa-actions rcsa-wide">
                <button type="submit" className="kn-btn kn-btn-primary" disabled={!periodOk}>{t("rcsa.wizard.next")}</button>
              </div>
            </form>
          </Card>
        ) : (
          <Card>
            <form className="rcsa-form" onSubmit={submit}>
              <label>{t("rcsa.wizard.framework")}
                <select required value={frameworkId} onChange={(e) => setFrameworkId(e.target.value)}>
                  <option value="">{t("rcsa.wizard.choose")}</option>
                  {frameworks.data?.map((f) => <option key={f.id} value={f.id}>{f.name_en} {f.version}</option>)}
                </select>
              </label>
              <label>{t("rcsa.wizard.profile")}
                <select required value={profileId} onChange={(e) => setProfileId(e.target.value)}>
                  <option value="">{t("rcsa.wizard.choose")}</option>
                  {profiles.data?.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.key} v{p.revision} — {t(`rcsa.status.${p.status}`)}
                    </option>
                  ))}
                </select>
              </label>
              <p className="kn-secondary rcsa-wide kn-m-0">{t("rcsa.wizard.profileHint")}</p>
              <ErrorNotice error={create.error} />
              <div className="rcsa-actions rcsa-wide">
                <button type="button" className="kn-btn kn-btn-secondary" onClick={() => setStep("period")}>{t("rcsa.wizard.back")}</button>
                <button type="submit" className="kn-btn kn-btn-primary" disabled={!frameworkId || !profileId || create.isPending}>
                  {t("rcsa.wizard.createDraft")}
                </button>
              </div>
            </form>
          </Card>
        )}
      </RcsaGate>
    </section>
  );
}
