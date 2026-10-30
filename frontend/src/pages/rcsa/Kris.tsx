import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { request } from "../../api";
import { useAuth } from "../../auth";
import { Badge, Card, DataTable, EmptyState, LoadingState, PageHeader } from "../../components/ui";
import { useControlSearch, withSelected, type ControlOption } from "../../controlSearch";
import { send, type ImplementationSnapshot, type Kri, type KriObservation, type Page } from "./api";
import { ScopeTarget } from "./ScopeTarget";
import { ErrorNotice, Pager, PersonSelect, RcsaGate, usePeople } from "./shared";

const LIMIT = 25;
function Band({ band }: { band?: KriObservation["band"] }) {
  const { t } = useTranslation();
  return band ? <Badge tone={band === "red" ? "ruby" : band === "amber" ? "amber" : "emerald"}>{t(`rcsa.kri.${band}Band`)}</Badge>
    : <span>{t("rcsa.kri.noData")}</span>;
}

function CreateKri() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { people } = usePeople();
  const [query, setQuery] = useState("");
  const controls = useControlSearch(query);
  const [control, setControl] = useState<ControlOption | null>(null);
  const [implementation, setImplementation] = useState("");
  const targets = useQuery({ queryKey: ["rcsa", "kri-targets", control?.id],
    queryFn: () => request<ImplementationSnapshot[]>(`/api/rcsa/kri-targets?control_id=${control!.id}`), enabled: !!control });
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [unit, setUnit] = useState("");
  const [direction, setDirection] = useState("high");
  const [amber, setAmber] = useState("");
  const [red, setRed] = useState("");
  const [owner, setOwner] = useState<number | null>(user?.id ?? null);
  const [days, setDays] = useState("7");
  const create = useMutation({ mutationFn: () => send<Kri>("/api/rcsa/kris", "POST", {
    name, source, unit, control_id: control!.id, implementation_id: implementation ? Number(implementation) : null,
    direction, amber_threshold: amber, red_threshold: red, owner_id: owner, decision_days: Number(days),
  }), onSuccess: (rule) => navigate(`/rcsa/kris/${rule.id}`) });
  const submit = (event: FormEvent) => { event.preventDefault(); create.mutate(); };
  return <Card title={t("rcsa.kri.new")}><form className="rcsa-form" onSubmit={submit}>
    <label>{t("rcsa.kri.name")}<input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} /></label>
    <label>{t("rcsa.kri.source")}<input required maxLength={200} value={source} onChange={(e) => setSource(e.target.value)} /></label>
    <label>{t("rcsa.kri.unit")}<input required maxLength={50} value={unit} onChange={(e) => setUnit(e.target.value)} /></label>
    <label>{t("rcsa.kri.search")}<input value={query} onChange={(e) => setQuery(e.target.value)} /></label>
    <label>{t("rcsa.scope.control")}<select required value={control?.id ?? ""} onChange={(e) => {
      setControl(withSelected(controls.data, control).find((c) => c.id === Number(e.target.value)) ?? null); setImplementation("");
    }}><option value="">{t("rcsa.wizard.choose")}</option>{withSelected(controls.data, control).map((c) => <option key={c.id} value={c.id}>{c.code} {c.title}</option>)}</select></label>
    <p className="kn-secondary">{t("rcsa.kri.searchHint")}</p>
    <label>{t("rcsa.kri.target")}<select value={implementation} onChange={(e) => setImplementation(e.target.value)} disabled={!control || targets.isPending}>
      <option value="">{t("rcsa.scope.wholeControl")}</option>
      {targets.data?.map((item) => <option key={item.id} value={item.id}>{item.asset ? `${item.asset.name} · ${item.asset.environment}` : t("rcsa.scope.process")}</option>)}
    </select></label>
    <label>{t("rcsa.kri.direction")}<select value={direction} onChange={(e) => setDirection(e.target.value)}>
      <option value="high">{t("rcsa.kri.high")}</option><option value="low">{t("rcsa.kri.low")}</option>
    </select></label>
    <label>{t("rcsa.kri.amber")}<input type="number" step="0.000001" required value={amber} onChange={(e) => setAmber(e.target.value)} /></label>
    <label>{t("rcsa.kri.red")}<input type="number" step="0.000001" required value={red} onChange={(e) => setRed(e.target.value)} /></label>
    <PersonSelect label={t("rcsa.triggers.owner")} people={people} value={owner} onChange={setOwner} filter={(p) => p.can_respond} allowEmpty={false} />
    <label>{t("rcsa.kri.days")}<input type="number" min={1} max={90} required value={days} onChange={(e) => setDays(e.target.value)} /></label>
    <p className="kn-secondary rcsa-wide">{t("rcsa.kri.definitionHint")}</p>
    <button className="kn-btn kn-btn-primary" disabled={!control || !owner || create.isPending}>{t("rcsa.kri.new")}</button>
    <div className="rcsa-wide"><ErrorNotice error={create.error || controls.error || targets.error} /></div>
  </form></Card>;
}

export function Kris() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [offset, setOffset] = useState(0);
  const rules = useQuery({ queryKey: ["rcsa", "kris", offset], queryFn: () => request<Page<Kri>>(`/api/rcsa/kris?limit=${LIMIT}&offset=${offset}`) });
  return <section className="kn-page"><PageHeader title={t("rcsa.kri.title")} subtitle={t("rcsa.kri.subtitle")} /><RcsaGate>
    {(user?.role === "admin" || user?.role === "grc_lead") && <CreateKri />}
    <Card title={t("rcsa.kri.title")}>
      <ErrorNotice error={rules.error} />{rules.isPending && <LoadingState />}
      {rules.data?.items.length === 0 && <EmptyState compact title={t("rcsa.kri.none")} />}
      <p className="kn-secondary">{t("rcsa.kri.statusHint")}</p>
      {rules.data && <><DataTable head={<tr><th>{t("rcsa.kri.name")}</th><th>{t("rcsa.scope.target")}</th><th>{t("rcsa.campaigns.status")}</th><th>{t("rcsa.kri.latest")}</th><th>{t("rcsa.kri.observed")}</th></tr>}>
        {rules.data.items.map((rule) => <tr key={rule.id}>
          <td><Link to={`/rcsa/kris/${rule.id}`}>{rule.name}</Link></td>
          <td>{rule.target_snapshot.control.code}<ScopeTarget snapshot={rule.target_snapshot.implementation} /></td>
          <td>{t(rule.enabled ? "rcsa.kri.enabled" : "rcsa.kri.disabled")}</td>
          <td><Band band={rule.latest?.band} /></td><td>{rule.latest?.observed_at ? new Date(rule.latest.observed_at).toLocaleString() : "—"}</td>
        </tr>)}
      </DataTable><Pager total={rules.data.total} limit={LIMIT} offset={offset} onChange={setOffset} /></>}
    </Card>
  </RcsaGate></section>;
}

function KriSettings({ rule, refresh }: { rule: Kri; refresh: () => Promise<void> }) {
  const { t } = useTranslation();
  const { people } = usePeople();
  const [owner, setOwner] = useState<number | null>(rule.owner_id);
  const [enabled, setEnabled] = useState(rule.enabled);
  const save = useMutation({ mutationFn: () => send(`/api/rcsa/kris/${rule.id}`, "PATCH", {
    enabled, owner_id: owner, expected_version: rule.version,
  }), onSuccess: refresh });
  return <Card title={t("rcsa.kri.settings")}><div className="rcsa-form">
    <PersonSelect label={t("rcsa.triggers.owner")} people={people} value={owner} onChange={setOwner} filter={(p) => p.can_respond} allowEmpty={false} />
    <label><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> {t("rcsa.kri.enable")}</label>
    <button className="kn-btn kn-btn-primary" disabled={save.isPending || !owner} onClick={() => save.mutate()}>{t("rcsa.kri.save")}</button>
    <ErrorNotice error={save.error} onReload={refresh} />
  </div></Card>;
}

function ObservationForm({ rule, refresh }: { rule: Kri; refresh: () => Promise<void> }) {
  const { t } = useTranslation();
  const [externalId, setExternalId] = useState("");
  const [value, setValue] = useState("");
  const [observed, setObserved] = useState("");
  const [sourceRef, setSourceRef] = useState("");
  const record = useMutation({ mutationFn: () => send<KriObservation>(`/api/rcsa/kris/${rule.id}/observations`, "POST", {
    external_id: externalId, value, observed_at: new Date(observed).toISOString(), source_ref: sourceRef,
  }), onSuccess: async () => { setExternalId(""); setValue(""); setObserved(""); setSourceRef(""); await refresh(); } });
  return <Card title={t("rcsa.kri.record")}><form className="rcsa-form" onSubmit={(e) => { e.preventDefault(); record.mutate(); }}>
    <label>{t("rcsa.kri.externalId")}<input required maxLength={200} value={externalId} onChange={(e) => setExternalId(e.target.value)} /></label>
    <label>{t("rcsa.kri.value")}<input required type="number" step="0.000001" value={value} onChange={(e) => setValue(e.target.value)} /></label>
    <label>{t("rcsa.kri.observed")}<input required type="datetime-local" value={observed} onChange={(e) => setObserved(e.target.value)} /></label>
    <label>{t("rcsa.kri.sourceRef")}<input required maxLength={500} value={sourceRef} onChange={(e) => setSourceRef(e.target.value)} /></label>
    <p className="kn-secondary rcsa-wide">{t("rcsa.kri.ingestHint")}</p>
    <button className="kn-btn kn-btn-primary" disabled={!rule.enabled || record.isPending}>{t("rcsa.kri.record")}</button>
    <ErrorNotice error={record.error} />
  </form></Card>;
}

export function KriDetail() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const id = Number(useParams().id);
  const client = useQueryClient();
  const [offset, setOffset] = useState(0);
  const rule = useQuery({ queryKey: ["rcsa", "kri", id], queryFn: () => request<Kri>(`/api/rcsa/kris/${id}`) });
  const history = useQuery({ queryKey: ["rcsa", "kri", id, "history", offset], queryFn: () => request<Page<KriObservation>>(`/api/rcsa/kris/${id}/observations?limit=${LIMIT}&offset=${offset}`) });
  const refresh = async () => { setOffset(0); await client.invalidateQueries({ queryKey: ["rcsa"] }); };
  const canManage = user?.role === "admin" || user?.role === "grc_lead";
  return <section className="kn-page"><RcsaGate>
    <Link to="/rcsa/kris">{t("rcsa.kri.back")}</Link><ErrorNotice error={rule.error} />{rule.isPending && <LoadingState />}
    {rule.data && <>
      <PageHeader title={rule.data.name} subtitle={`${rule.data.source} · ${rule.data.unit}`} />
      <Card title={t("rcsa.scope.target")}><p>{rule.data.target_snapshot.control.code} {rule.data.target_snapshot.control.title}</p>
        <ScopeTarget snapshot={rule.data.target_snapshot.implementation} details />
        <p>{t(rule.data.direction === "high" ? "rcsa.kri.high" : "rcsa.kri.low")} · {t("rcsa.kri.amber")}: {rule.data.amber_threshold} · {t("rcsa.kri.red")}: {rule.data.red_threshold}</p>
        <p className="kn-secondary">{t("rcsa.kri.definitionHint")}</p>
      </Card>
      {canManage && <><KriSettings key={rule.data.version} rule={rule.data} refresh={refresh} /><ObservationForm rule={rule.data} refresh={refresh} /></>}
    </>}
    <Card title={t("rcsa.kri.history")}><ErrorNotice error={history.error} />{history.isPending && <LoadingState />}
      <p className="kn-secondary">{t("rcsa.kri.statusHint")}</p>
      {history.data?.items.length === 0 && <EmptyState compact title={t("rcsa.kri.noData")} />}
      {history.data && <><DataTable head={<tr><th>{t("rcsa.kri.observed")}</th><th>{t("rcsa.kri.value")}</th><th>{t("rcsa.kri.latest")}</th><th>{t("rcsa.kri.sourceRef")}</th><th>{t("rcsa.kri.event")}</th></tr>}>
        {history.data.items.map((row) => <tr key={row.id}><td>{new Date(row.observed_at).toLocaleString()}</td><td>{row.value}</td><td><Band band={row.band} /></td><td>{row.source_ref}</td>
          <td>{row.trigger_id ? <Link to={`/rcsa/triggers/${row.trigger_id}`}>#{row.trigger_id}</Link> : "—"}</td></tr>)}
      </DataTable><Pager total={history.data.total} offset={offset} limit={LIMIT} onChange={setOffset} /></>}
    </Card>
  </RcsaGate></section>;
}
