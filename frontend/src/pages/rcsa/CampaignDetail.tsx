// 活动详情：范围与分派、评估项、关联触发；负责人在这里做状态转换。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { useAuth } from "../../auth";
import { Card, DataTable, EmptyState, LoadingState, PageHeader } from "../../components/ui";
import { api, newKey, send, type Campaign, type Item } from "./api";
import { StepIndicator } from "./CampaignNew";
import { ErrorNotice, PersonSelect, RcsaGate, StatusBadge, Tabs, usePeople } from "./shared";

type Tab = "scope" | "items" | "triggers";

function useRefresh(campaignId: number) {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["rcsa", "campaign", campaignId] });
}

function Meta({ campaign }: { campaign: Campaign }) {
  const { t } = useTranslation();
  const { nameOf } = usePeople();
  const rows: [string, string][] = [
    [t("rcsa.campaigns.period"), `${campaign.period_start} – ${campaign.period_end}`],
    [t("rcsa.wizard.dueAt"), campaign.due_at],
    [t("rcsa.wizard.timezone"), campaign.timezone],
    [t("rcsa.campaigns.owner"), nameOf(campaign.owner_id)],
    [t("rcsa.wizard.framework"), campaign.framework_snapshot
      ? `${campaign.framework_snapshot.name} ${campaign.framework_snapshot.version}` : t("rcsa.notFrozen")],
    [t("rcsa.wizard.profile"), campaign.profile_snapshot
      ? `${campaign.profile_snapshot.key} v${campaign.profile_snapshot.revision} · ${campaign.profile_snapshot.horizon}`
      : t("rcsa.notFrozen")],
  ];
  return (
    <dl className="rcsa-meta">
      {rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
  );
}

function Transitions({ campaign }: { campaign: Campaign }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const refresh = useRefresh(campaign.id);
  const [reason, setReason] = useState("");
  const [overdue, setOverdue] = useState("");
  const [evidence, setEvidence] = useState("");
  const move = useMutation({
    mutationFn: ({ name, body, key }: { name: string; body?: object; key?: string }) =>
      send<Campaign>(`/api/rcsa/campaigns/${campaign.id}/${name}`, "POST",
        { expected_version: campaign.version, ...body }, key),
    onSuccess: () => { setReason(""); return refresh(); },
  });
  if (user?.id !== campaign.owner_id || campaign.status === "closed" || campaign.status === "cancelled") return null;
  const busy = move.isPending;
  return (
    <Card title={t("rcsa.transitions.title")} className="rcsa-no-print">
      <div className="rcsa-actions">
        {campaign.status === "draft" && (
          <button type="button" className="kn-btn kn-btn-primary" disabled={busy}
            onClick={() => move.mutate({ name: "activate", key: newKey() })}>{t("rcsa.transitions.activate")}</button>
        )}
        {campaign.status === "active" && (
          <button type="button" className="kn-btn kn-btn-primary" disabled={busy}
            onClick={() => move.mutate({ name: "begin-review" })}>{t("rcsa.transitions.beginReview")}</button>
        )}
      </div>
      {campaign.status === "in_review" && (
        <div className="rcsa-form kn-mt-1">
          <label>{t("rcsa.transitions.overdueAck")}
            <textarea rows={2} value={overdue} onChange={(e) => setOverdue(e.target.value)} />
          </label>
          <label>{t("rcsa.transitions.evidenceAck")}
            <textarea rows={2} value={evidence} onChange={(e) => setEvidence(e.target.value)} />
          </label>
          <div className="rcsa-actions rcsa-wide">
            <button type="button" className="kn-btn kn-btn-primary" disabled={busy}
              onClick={() => move.mutate({
                name: "close", key: newKey(),
                body: { overdue_acknowledgement: overdue, evidence_acknowledgement: evidence },
              })}>{t("rcsa.transitions.close")}</button>
          </div>
        </div>
      )}
      <div className="rcsa-form kn-mt-1">
        <label className="rcsa-wide">{t("rcsa.transitions.reason")}
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <div className="rcsa-actions rcsa-wide">
          {campaign.status === "in_review" && (
            <button type="button" className="kn-btn kn-btn-secondary" disabled={busy || !reason.trim()}
              onClick={() => move.mutate({ name: "return", body: { reason } })}>{t("rcsa.transitions.return")}</button>
          )}
          <button type="button" className="kn-btn kn-btn-danger" disabled={busy || !reason.trim()}
            onClick={() => move.mutate({ name: "cancel", body: { reason } })}>{t("rcsa.transitions.cancel")}</button>
        </div>
      </div>
      <ErrorNotice error={move.error} onReload={refresh} />
    </Card>
  );
}

function ScopePicker({ campaign }: { campaign: Campaign }) {
  const { t } = useTranslation();
  const refresh = useRefresh(campaign.id);
  const { people } = usePeople();
  const scope = useQuery({ queryKey: ["rcsa", "campaign", campaign.id, "scope"], queryFn: () => api.scope(campaign.id) });
  const [picked, setPicked] = useState<number[]>([]);
  const [reviewer, setReviewer] = useState<number | null>(null);
  const add = useMutation({
    mutationFn: () => send(`/api/rcsa/campaigns/${campaign.id}/items`, "POST",
      { control_ids: picked, ...(reviewer ? { reviewer_id: reviewer } : {}) }),
    onSuccess: () => { setPicked([]); return refresh(); },
  });
  if (scope.isPending) return <LoadingState />;
  const candidates = (scope.data ?? []).filter((row) => !row.included);
  return (
    <Card title={t("rcsa.scope.candidates")}>
      <p className="kn-secondary">{t("rcsa.scope.hint")}</p>
      {candidates.length === 0 ? <EmptyState compact title={t("rcsa.scope.noCandidates")} /> : (
        <DataTable className="kn-m-0" head={<tr><th /><th>{t("rcsa.scope.control")}</th><th>{t("rcsa.scope.items")}</th></tr>}>
          {candidates.map((row) => (
            <tr key={row.control_id}>
              <td>
                <input type="checkbox" aria-label={t("rcsa.scope.pick", { code: row.code })}
                  checked={picked.includes(row.control_id)}
                  onChange={(e) => setPicked(e.target.checked ? [...picked, row.control_id] : picked.filter((id) => id !== row.control_id))} />
              </td>
              <td><strong>{row.code}</strong> {row.title}</td>
              <td>{row.framework_items.join(", ")}</td>
            </tr>
          ))}
        </DataTable>
      )}
      <div className="rcsa-form kn-mt-1">
        <PersonSelect label={t("rcsa.scope.defaultReviewer")} value={reviewer} onChange={setReviewer} people={people}
          filter={(p) => p.can_review} />
        <div className="rcsa-actions">
          <button type="button" className="kn-btn kn-btn-primary" disabled={!picked.length || add.isPending} onClick={() => add.mutate()}>
            {t("rcsa.scope.add", { count: picked.length })}
          </button>
        </div>
      </div>
      <ErrorNotice error={add.error} />
    </Card>
  );
}

function ItemRow({ item, campaign, editable }: { item: Item; campaign: Campaign; editable: boolean }) {
  const { t } = useTranslation();
  const refresh = useRefresh(campaign.id);
  const { people, nameOf } = usePeople();
  const [assignee, setAssignee] = useState(item.assignee_id);
  const [reviewer, setReviewer] = useState(item.reviewer_id);
  const [reason, setReason] = useState("");
  const changed = assignee !== item.assignee_id || reviewer !== item.reviewer_id;
  const save = useMutation({
    mutationFn: () => send(`/api/rcsa/items/${item.id}/assign`, "POST", {
      ...(assignee !== item.assignee_id && assignee ? { assignee_id: assignee } : {}),
      ...(reviewer !== item.reviewer_id && reviewer ? { reviewer_id: reviewer } : {}),
      reason, expected_version: item.version,
    }),
    onSuccess: () => { setReason(""); return refresh(); },
  });
  const remove = useMutation({
    mutationFn: () => send(`/api/rcsa/items/${item.id}`, "DELETE", {}),
    onSuccess: refresh,
  });
  const code = item.control_snapshot?.code ?? `#${item.control_id}`;
  return (
    <tr>
      <td>
        {campaign.status === "draft" ? <strong>{code}</strong> : <Link to={`/rcsa/items/${item.id}`}>{code}</Link>}{" "}
        {item.control_snapshot?.title}
      </td>
      <td>{editable ? <PersonSelect label={t("rcsa.scope.assignee")} value={assignee} onChange={setAssignee} people={people} filter={(p) => p.can_respond} /> : nameOf(item.assignee_id)}</td>
      <td>{editable ? <PersonSelect label={t("rcsa.scope.reviewer")} value={reviewer} onChange={setReviewer} people={people} filter={(p) => p.can_review} /> : nameOf(item.reviewer_id)}</td>
      <td><StatusBadge status={item.status} /></td>
      {editable && (
        <td>
          {campaign.status === "active" && changed && (
            <input aria-label={t("rcsa.transitions.reason")} placeholder={t("rcsa.transitions.reason")} value={reason} onChange={(e) => setReason(e.target.value)} />
          )}
          <div className="rcsa-actions">
            <button type="button" className="kn-btn-sm" disabled={!changed || save.isPending} onClick={() => save.mutate()}>{t("common.save")}</button>
            {campaign.status === "draft" && (
              <button type="button" className="kn-btn-sm kn-btn-danger" disabled={remove.isPending} onClick={() => remove.mutate()}>{t("rcsa.scope.remove")}</button>
            )}
          </div>
          <ErrorNotice error={save.error ?? remove.error} onReload={refresh} />
        </td>
      )}
    </tr>
  );
}

function Items({ campaign }: { campaign: Campaign }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const items = useQuery({ queryKey: ["rcsa", "campaign", campaign.id, "items"], queryFn: () => api.items(campaign.id) });
  const editable = (user?.role === "admin" || user?.role === "grc_lead") && (campaign.status === "draft" || campaign.status === "active");
  if (items.isPending) return <LoadingState />;
  if (items.error) return <ErrorNotice error={items.error} />;
  if (items.data.length === 0) return <EmptyState compact title={t("rcsa.scope.empty")} />;
  return (
    <DataTable className="kn-m-0" head={
      <tr>
        <th>{t("rcsa.scope.control")}</th><th>{t("rcsa.scope.assignee")}</th>
        <th>{t("rcsa.scope.reviewer")}</th><th>{t("rcsa.campaigns.status")}</th>
        {editable && <th>{t("rcsa.scope.change")}</th>}
      </tr>
    }>
      {items.data.map((item) => <ItemRow key={`${item.id}-${item.version}`} item={item} campaign={campaign} editable={editable} />)}
    </DataTable>
  );
}

function LinkedTriggers({ campaign }: { campaign: Campaign }) {
  const { t } = useTranslation();
  const triggers = useQuery({ queryKey: ["rcsa", "triggers", "all"], queryFn: () => api.triggers(0, 100) });
  const linked = (triggers.data?.items ?? []).filter((trigger) => trigger.campaign_id === campaign.id);
  if (triggers.isPending) return <LoadingState />;
  if (linked.length === 0) return <EmptyState compact title={t("rcsa.triggers.noneLinked")} />;
  return (
    <ul className="kn-stack-1">
      {linked.map((trigger) => (
        <li key={trigger.id}><Link to={`/rcsa/triggers/${trigger.id}`}>{trigger.title}</Link> <StatusBadge status={trigger.status} /></li>
      ))}
    </ul>
  );
}

export function CampaignDetail() {
  const { t } = useTranslation();
  const id = Number(useParams().id);
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || (/* 草稿先看范围 */ "scope");
  const campaign = useQuery({ queryKey: ["rcsa", "campaign", id], queryFn: () => api.campaign(id) });
  return (
    <section className="kn-page">
      <RcsaGate>
        {campaign.isPending && <LoadingState />}
        <ErrorNotice error={campaign.error} />
        {campaign.data && (
          <>
            <PageHeader
              title={campaign.data.name}
              badge={<StatusBadge status={campaign.data.status} />}
              subtitle={campaign.data.status_reason || undefined}
              actions={<Link className="kn-btn kn-btn-secondary" to={`/rcsa/campaigns/${id}/report`}>{t("rcsa.report.open")}</Link>}
            />
            {campaign.data.status === "draft" && <StepIndicator current="scope" />}
            <Meta campaign={campaign.data} />
            <Transitions campaign={campaign.data} />
            <Tabs<Tab>
              label={t("rcsa.campaigns.sections")}
              value={tab}
              onChange={(value) => setParams({ tab: value })}
              options={[
                { value: "scope", label: t("rcsa.tabs.scope") },
                { value: "items", label: t("rcsa.tabs.items") },
                { value: "triggers", label: t("rcsa.tabs.triggers") },
              ]}
            />
            {tab === "scope" && (
              <div className="kn-stack-3">
                {campaign.data.status === "draft" && <ScopePicker campaign={campaign.data} />}
                <Card title={t("rcsa.scope.inScope")}><Items campaign={campaign.data} /></Card>
              </div>
            )}
            {tab === "items" && <Card title={t("rcsa.tabs.items")}><Items campaign={campaign.data} /></Card>}
            {tab === "triggers" && <Card title={t("rcsa.tabs.triggers")}><LinkedTriggers campaign={campaign.data} /></Card>}
          </>
        )}
      </RcsaGate>
    </section>
  );
}
