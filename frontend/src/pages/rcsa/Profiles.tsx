// 评分档案：新建修订版、补全口径并批准。已批准的版本只读；示例标签与阈值不是机构已批准的口径。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useAuth } from "../../auth";
import { Card, DataTable, EmptyState, LoadingState, PageHeader } from "../../components/ui";
import { api, send, type Band, type Profile } from "./api";
import { ErrorNotice, RcsaGate, StatusBadge, useUnsavedGuard } from "./shared";

const LEVELS = [1, 2, 3, 4, 5];

function Calibrate({ profile, onDone }: { profile: Profile; onDone: () => void }) {
  const { t } = useTranslation();
  const defs = profile.definitions;
  const [horizon, setHorizon] = useState(profile.horizon);
  const [likelihood, setLikelihood] = useState(
    LEVELS.map((level) => defs.likelihood?.find((l) => l.level === level) ?? { level, label: "", criteria: "" }),
  );
  const [dimensions, setDimensions] = useState((defs.impact_dimensions ?? []).join(", "));
  const [impact, setImpact] = useState(
    LEVELS.map((level) => defs.impact?.find((l) => l.level === level) ?? { level, label: "", criteria: {} as Record<string, string> }),
  );
  const [bands, setBands] = useState<Band[]>(profile.bands);
  const [dirty, setDirty] = useState(false);
  useUnsavedGuard(dirty);
  const dims = dimensions.split(",").map((d) => d.trim()).filter(Boolean);
  const save = useMutation({
    mutationFn: () => send<Profile>(`/api/rcsa/profiles/${profile.id}`, "PATCH", {
      horizon, bands, expected_version: profile.version,
      definitions: { ...defs, likelihood, impact, impact_dimensions: dims },
    }),
    onSuccess: () => { setDirty(false); onDone(); },
  });
  const approve = useMutation({
    mutationFn: () => send<Profile>(`/api/rcsa/profiles/${profile.id}/approve`, "POST", { expected_version: profile.version }),
    onSuccess: onDone,
  });
  const touch = <T,>(setter: (v: T) => void) => (value: T) => { setter(value); setDirty(true); };
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(); };
  return (
    <form className="kn-stack-3" onSubmit={submit} aria-label={t("rcsa.profiles.calibrate")}>
      <label className="kn-block">{t("rcsa.profiles.horizon")}
        <input value={horizon} onChange={(e) => touch(setHorizon)(e.target.value)} placeholder={t("rcsa.profiles.horizonHint")} />
      </label>
      <fieldset>
        <legend>{t("rcsa.profiles.likelihood")}</legend>
        {likelihood.map((level, index) => (
          <div key={level.level} className="rcsa-form">
            <label>{t("rcsa.profiles.level", { level: level.level })}
              <input value={level.label} onChange={(e) => touch(setLikelihood)(likelihood.map((l, i) => i === index ? { ...l, label: e.target.value } : l))} />
            </label>
            <label className="rcsa-wide">{t("rcsa.profiles.criteria")}
              <input value={level.criteria} onChange={(e) => touch(setLikelihood)(likelihood.map((l, i) => i === index ? { ...l, criteria: e.target.value } : l))} />
            </label>
          </div>
        ))}
      </fieldset>
      <label className="kn-block">{t("rcsa.profiles.dimensions")}
        <input value={dimensions} onChange={(e) => touch(setDimensions)(e.target.value)} placeholder={t("rcsa.profiles.dimensionsHint")} />
      </label>
      <fieldset>
        <legend>{t("rcsa.profiles.impact")}</legend>
        {impact.map((level, index) => (
          <div key={level.level} className="rcsa-form">
            <label>{t("rcsa.profiles.level", { level: level.level })}
              <input value={level.label} onChange={(e) => touch(setImpact)(impact.map((l, i) => i === index ? { ...l, label: e.target.value } : l))} />
            </label>
            {dims.map((dim) => (
              <label key={dim}>{dim}
                <input value={level.criteria[dim] ?? ""} onChange={(e) => touch(setImpact)(impact.map((l, i) => i === index ? { ...l, criteria: { ...l.criteria, [dim]: e.target.value } } : l))} />
              </label>
            ))}
          </div>
        ))}
      </fieldset>
      <fieldset>
        <legend>{t("rcsa.profiles.bands")}</legend>
        {bands.map((band, index) => (
          <div key={index} className="rcsa-form">
            <label>{t("rcsa.profiles.bandLabel")}<input value={band.label} onChange={(e) => touch(setBands)(bands.map((b, i) => i === index ? { ...b, label: e.target.value } : b))} /></label>
            <label>{t("rcsa.profiles.min")}<input type="number" min={1} max={25} value={band.min} onChange={(e) => touch(setBands)(bands.map((b, i) => i === index ? { ...b, min: Number(e.target.value) } : b))} /></label>
            <label>{t("rcsa.profiles.max")}<input type="number" min={1} max={25} value={band.max} onChange={(e) => touch(setBands)(bands.map((b, i) => i === index ? { ...b, max: Number(e.target.value) } : b))} /></label>
          </div>
        ))}
      </fieldset>
      <div className="rcsa-actions">
        <button type="submit" className="kn-btn kn-btn-secondary" disabled={!dirty || save.isPending}>{t("common.save")}</button>
        <button type="button" className="kn-btn kn-btn-primary" disabled={dirty || approve.isPending} onClick={() => approve.mutate()}>{t("rcsa.profiles.approve")}</button>
      </div>
      <ErrorNotice error={save.error ?? approve.error} onReload={onDone} />
      {approve.error ? <p className="kn-secondary kn-text-xs">{t("rcsa.profiles.incompleteHint")}</p> : null}
    </form>
  );
}

export function Profiles() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const canManage = user?.role === "admin" || user?.role === "grc_lead";
  const profiles = useQuery({ queryKey: ["rcsa", "profiles"], queryFn: api.profiles });
  const [selected, setSelected] = useState<number | null>(null);
  const [key, setKey] = useState("");
  const [base, setBase] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["rcsa", "profiles"] });
  const create = useMutation({
    mutationFn: () => send<Profile>("/api/rcsa/profiles", "POST", { key, ...(base ? { base_profile_id: Number(base) } : {}) }),
    onSuccess: (profile) => { setSelected(profile.id); setKey(""); return refresh(); },
  });
  const current = profiles.data?.find((p) => p.id === selected);
  return (
    <section className="kn-page">
      <PageHeader title={t("rcsa.profiles.title")} subtitle={t("rcsa.profiles.subtitle")} />
      <RcsaGate>
        {profiles.isPending && <LoadingState />}
        <Card title={t("rcsa.profiles.list")}>
          {profiles.data?.length === 0 && <EmptyState compact title={t("rcsa.profiles.none")} description={t("rcsa.profiles.seedHint")} />}
          {profiles.data && profiles.data.length > 0 && (
            <DataTable className="kn-m-0" head={<tr><th>{t("rcsa.profiles.key")}</th><th>{t("rcsa.profiles.horizon")}</th><th>{t("rcsa.campaigns.status")}</th><th /></tr>}>
              {profiles.data.map((p) => (
                <tr key={p.id}>
                  <td>{p.key} v{p.revision}</td>
                  <td>{p.horizon || "—"}</td>
                  <td><StatusBadge status={p.status} /></td>
                  <td><button type="button" className="kn-btn-sm" onClick={() => setSelected(p.id)}>{p.status === "draft" && canManage ? t("rcsa.profiles.calibrate") : t("rcsa.profiles.view")}</button></td>
                </tr>
              ))}
            </DataTable>
          )}
        </Card>
        {canManage && (
          <Card title={t("rcsa.profiles.newRevision")}>
            <form className="rcsa-form" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
              <label>{t("rcsa.profiles.key")}<input required pattern="[a-z0-9][a-z0-9._\-]*" value={key} onChange={(e) => setKey(e.target.value)} /></label>
              <label>{t("rcsa.profiles.copyFrom")}
                <select value={base} onChange={(e) => setBase(e.target.value)}>
                  <option value="">{t("rcsa.profiles.startDefault")}</option>
                  {profiles.data?.map((p) => <option key={p.id} value={p.id}>{p.key} v{p.revision}</option>)}
                </select>
              </label>
              <div className="rcsa-actions"><button type="submit" className="kn-btn kn-btn-secondary" disabled={create.isPending}>{t("rcsa.profiles.create")}</button></div>
              <div className="rcsa-wide"><ErrorNotice error={create.error} /></div>
            </form>
          </Card>
        )}
        {current && (
          <Card title={`${current.key} v${current.revision}`} actions={<StatusBadge status={current.status} />}>
            <p className="kn-secondary kn-text-xs">{t("rcsa.profiles.notPolicy")}</p>
            {current.status === "draft" && canManage
              ? <Calibrate key={`${current.id}-${current.version}`} profile={current} onDone={refresh} />
              : (
                <ul className="kn-stack-1">
                  <li>{t("rcsa.profiles.horizon")}: {current.horizon}</li>
                  {current.bands.map((b) => <li key={b.label}>{b.label}: {b.min}–{b.max}</li>)}
                </ul>
              )}
          </Card>
        )}
      </RcsaGate>
    </section>
  );
}
