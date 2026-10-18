// 手动触发：登记、分诊、决定重评或不重评、关联活动、由另一人确认。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { request } from "../../api";
import { useAuth } from "../../auth";
import { Card, DataTable, EmptyState, LoadingState, PageHeader } from "../../components/ui";
import { api, send, type Trigger } from "./api";
import { ErrorNotice, Pager, PersonSelect, RcsaGate, StatusBadge, usePeople } from "./shared";

const LIMIT = 25;

interface ControlRow {
  id: number;
  code: string;
  title: string;
}

function RegisterForm() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { people } = usePeople();
  const controls = useQuery({ queryKey: ["controls", "all"], queryFn: () => request<ControlRow[]>("/api/controls?limit=500") });
  const [type, setType] = useState<Trigger["trigger_type"]>("incident");
  const [title, setTitle] = useState("");
  const [source, setSource] = useState("");
  const [occurred, setOccurred] = useState("");
  const [planned, setPlanned] = useState("");
  const [owner, setOwner] = useState<number | null>(user?.id ?? null);
  const [due, setDue] = useState("");
  const [picked, setPicked] = useState<number[]>([]);
  const create = useMutation({
    mutationFn: () => send<Trigger>("/api/rcsa/triggers", "POST", {
      trigger_type: type, title, source_ref: source, occurred_at: new Date(occurred).toISOString(),
      ...(planned ? { planned_effective_at: new Date(planned).toISOString() } : {}),
      owner_id: owner, decision_due_at: due, control_ids: picked,
    }),
    onSuccess: (trigger) => navigate(`/rcsa/triggers/${trigger.id}`),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate(); };
  return (
    <Card title={t("rcsa.triggers.register")}>
      <form className="rcsa-form" onSubmit={submit}>
        <label>{t("rcsa.triggers.type")}
          <select value={type} onChange={(e) => setType(e.target.value as Trigger["trigger_type"])}>
            {(["incident", "planned_change", "other"] as const).map((v) => <option key={v} value={v}>{t(`rcsa.triggers.types.${v}`)}</option>)}
          </select>
        </label>
        <label className="rcsa-wide">{t("rcsa.triggers.titleField")}<input required value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <label>{t("rcsa.triggers.source")}<input required value={source} onChange={(e) => setSource(e.target.value)} placeholder={t("rcsa.triggers.sourceHint")} /></label>
        <label>{t("rcsa.triggers.occurred")}<input type="datetime-local" required value={occurred} onChange={(e) => setOccurred(e.target.value)} /></label>
        {type === "planned_change" && (
          <label>{t("rcsa.triggers.planned")}<input type="datetime-local" required value={planned} onChange={(e) => setPlanned(e.target.value)} /></label>
        )}
        <PersonSelect label={t("rcsa.triggers.owner")} value={owner} onChange={setOwner} people={people} filter={(p) => p.can_respond} allowEmpty={false} />
        <label>{t("rcsa.triggers.decisionDue")}<input type="date" required value={due} onChange={(e) => setDue(e.target.value)} /></label>
        <label className="rcsa-wide">{t("rcsa.triggers.controls")}
          <select multiple size={6} value={picked.map(String)}
            onChange={(e) => setPicked(Array.from(e.target.selectedOptions, (o) => Number(o.value)))}>
            {controls.data?.map((c) => <option key={c.id} value={c.id}>{c.code} {c.title}</option>)}
          </select>
        </label>
        <p className="kn-secondary kn-text-xs rcsa-wide kn-m-0">{t("rcsa.triggers.registerHint")}</p>
        <div className="rcsa-actions rcsa-wide">
          <button type="submit" className="kn-btn kn-btn-primary" disabled={create.isPending || !owner}>{t("rcsa.triggers.register")}</button>
        </div>
        <div className="rcsa-wide"><ErrorNotice error={create.error} /></div>
      </form>
    </Card>
  );
}

export function Triggers() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [offset, setOffset] = useState(0);
  const triggers = useQuery({ queryKey: ["rcsa", "triggers", offset], queryFn: () => api.triggers(offset, LIMIT) });
  const canRegister = user?.role !== "viewer";
  return (
    <section className="kn-page">
      <PageHeader title={t("rcsa.triggers.title")} subtitle={t("rcsa.triggers.subtitle")} />
      <RcsaGate>
        {canRegister && <RegisterForm />}
        <Card title={t("rcsa.triggers.list")}>
          {triggers.isPending && <LoadingState />}
          <ErrorNotice error={triggers.error} />
          {triggers.data?.items.length === 0 && <EmptyState compact title={t("rcsa.triggers.none")} />}
          {triggers.data && triggers.data.items.length > 0 && (
            <DataTable className="kn-m-0" head={<tr><th>{t("rcsa.triggers.titleField")}</th><th>{t("rcsa.triggers.type")}</th><th>{t("rcsa.triggers.decisionDue")}</th><th>{t("rcsa.campaigns.status")}</th></tr>}>
              {triggers.data.items.map((trigger) => (
                <tr key={trigger.id}>
                  <td><Link to={`/rcsa/triggers/${trigger.id}`}>{trigger.title}</Link></td>
                  <td>{t(`rcsa.triggers.types.${trigger.trigger_type}`)}</td>
                  <td>{trigger.decision_due_at}</td>
                  <td><StatusBadge status={trigger.status} /></td>
                </tr>
              ))}
            </DataTable>
          )}
          {triggers.data && <Pager total={triggers.data.total} offset={offset} limit={LIMIT} onChange={setOffset} />}
        </Card>
      </RcsaGate>
    </section>
  );
}

export function TriggerDetail() {
  const { t } = useTranslation();
  const id = Number(useParams().id);
  const { user } = useAuth();
  const { nameOf } = usePeople();
  const queryClient = useQueryClient();
  const trigger = useQuery({ queryKey: ["rcsa", "trigger", id], queryFn: () => api.trigger(id) });
  const campaigns = useQuery({ queryKey: ["rcsa", "campaigns", "linkable"], queryFn: () => api.campaigns("", 0, 100) });
  const [rationale, setRationale] = useState("");
  const [campaignId, setCampaignId] = useState("");
  const [exclusions, setExclusions] = useState<Record<number, string>>({});
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["rcsa", "trigger", id] });
  const step = useMutation({
    mutationFn: ({ name, body }: { name: string; body?: object }) =>
      send<Trigger>(`/api/rcsa/triggers/${id}/${name}`, "POST", { expected_version: trigger.data!.version, ...body }),
    onSuccess: () => { setRationale(""); return refresh(); },
  });
  if (trigger.isPending) return <section className="kn-page"><LoadingState /></section>;
  if (trigger.error) return <section className="kn-page"><ErrorNotice error={trigger.error} /></section>;
  const data = trigger.data;
  const isOwner = user?.id === data.owner_id;
  const canConfirm = data.status === "no_reassessment" && data.proposed_by !== user?.id
    && (user?.role === "admin" || user?.role === "grc_lead");
  const linkable = (campaigns.data?.items ?? []).filter((c) => c.status !== "closed" && c.status !== "cancelled");
  return (
    <section className="kn-page">
      <RcsaGate>
        <PageHeader title={data.title} badge={<StatusBadge status={data.status} />}
          subtitle={`${t(`rcsa.triggers.types.${data.trigger_type}`)} · ${data.source_ref}`} />
        <dl className="rcsa-meta">
          <div><dt>{t("rcsa.triggers.owner")}</dt><dd>{nameOf(data.owner_id)}</dd></div>
          <div><dt>{t("rcsa.triggers.occurred")}</dt><dd>{data.occurred_at.slice(0, 16).replace("T", " ")}</dd></div>
          {data.planned_effective_at && <div><dt>{t("rcsa.triggers.planned")}</dt><dd>{data.planned_effective_at.slice(0, 10)}</dd></div>}
          <div><dt>{t("rcsa.triggers.decisionDue")}</dt><dd>{data.decision_due_at}</dd></div>
          {data.campaign_id && <div><dt>{t("rcsa.triggers.campaign")}</dt><dd><Link to={`/rcsa/campaigns/${data.campaign_id}`}>#{data.campaign_id}</Link></dd></div>}
        </dl>
        <p className="kn-secondary kn-text-xs">{t("rcsa.triggers.limits")}</p>
        <Card title={t("rcsa.triggers.controls")}>
          {data.controls.length === 0 ? <EmptyState compact title={t("rcsa.triggers.noControls")} /> : (
            <ul className="kn-stack-1">
              {data.controls.map((c) => (
                <li key={c.id}>
                  <strong>{c.control_snapshot.code}</strong> {c.control_snapshot.title}{" "}
                  <StatusBadge status={c.coverage_disposition === "assess" ? "active" : c.coverage_disposition === "excluded" ? "excluded" : "pending"} />
                  {c.coverage_reason ? <span className="kn-secondary"> — {c.coverage_reason}</span> : null}
                  {isOwner && data.status === "awaiting_assessment" && (
                    <input aria-label={t("rcsa.triggers.excludeReason", { code: c.control_snapshot.code })}
                      placeholder={t("rcsa.triggers.excludeReasonHint")}
                      value={exclusions[c.control_id] ?? ""}
                      onChange={(e) => setExclusions({ ...exclusions, [c.control_id]: e.target.value })} />
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
        {data.rationale && <Card title={t("rcsa.triggers.rationale")}><p className="kn-m-0">{data.rationale}</p></Card>}
        {(isOwner || canConfirm) && (
          <Card title={t("rcsa.triggers.next")}>
            {isOwner && data.status === "registered" && (
              <button type="button" className="kn-btn kn-btn-primary" disabled={step.isPending} onClick={() => step.mutate({ name: "triage" })}>{t("rcsa.triggers.triage")}</button>
            )}
            {isOwner && data.status === "triaged" && (
              <div className="rcsa-form">
                <label className="rcsa-wide">{t("rcsa.triggers.rationale")}<textarea rows={2} value={rationale} onChange={(e) => setRationale(e.target.value)} /></label>
                <div className="rcsa-actions rcsa-wide">
                  <button type="button" className="kn-btn kn-btn-primary" disabled={step.isPending} onClick={() => step.mutate({ name: "decide", body: { decision: "reassess", rationale } })}>{t("rcsa.triggers.reassess")}</button>
                  <button type="button" className="kn-btn kn-btn-secondary" disabled={step.isPending || !rationale.trim()} onClick={() => step.mutate({ name: "decide", body: { decision: "no_reassessment", rationale } })}>{t("rcsa.triggers.noReassessment")}</button>
                </div>
              </div>
            )}
            {isOwner && data.status === "awaiting_assessment" && (
              <div className="rcsa-form">
                <label>{t("rcsa.triggers.campaign")}
                  <select value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
                    <option value="">{t("rcsa.wizard.choose")}</option>
                    {linkable.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                </label>
                <div className="rcsa-actions">
                  <button type="button" className="kn-btn kn-btn-primary" disabled={!campaignId || step.isPending}
                    onClick={() => step.mutate({ name: "link", body: {
                      campaign_id: Number(campaignId),
                      exclusions: Object.entries(exclusions).filter(([, reason]) => reason.trim()).map(([control, reason]) => ({ control_id: Number(control), reason })),
                    } })}>{t("rcsa.triggers.link")}</button>
                </div>
                <p className="kn-secondary kn-text-xs rcsa-wide kn-m-0">{t("rcsa.triggers.linkHint")}</p>
              </div>
            )}
            {canConfirm && (
              <div className="rcsa-form">
                <label className="rcsa-wide">{t("rcsa.review.reason")}<input value={rationale} onChange={(e) => setRationale(e.target.value)} /></label>
                <div className="rcsa-actions rcsa-wide">
                  <button type="button" className="kn-btn kn-btn-primary" disabled={step.isPending} onClick={() => step.mutate({ name: "review", body: { decision: "approve", reason: rationale } })}>{t("rcsa.triggers.confirm")}</button>
                  <button type="button" className="kn-btn kn-btn-secondary" disabled={step.isPending || !rationale.trim()} onClick={() => step.mutate({ name: "review", body: { decision: "reject", reason: rationale } })}>{t("rcsa.triggers.reject")}</button>
                </div>
              </div>
            )}
            <ErrorNotice error={step.error} onReload={refresh} />
          </Card>
        )}
      </RcsaGate>
    </section>
  );
}
