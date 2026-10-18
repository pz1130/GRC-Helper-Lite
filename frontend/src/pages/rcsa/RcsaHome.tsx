// RCSA 首页：我的待办（逾期置顶）与活动列表。
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { useAuth } from "../../auth";
import { Card, DataTable, EmptyState, LoadingState, PageHeader } from "../../components/ui";
import { api, type CampaignStatus, type WorkKind, type WorkRow } from "./api";
import { ErrorNotice, Pager, RcsaGate, StatusBadge } from "./shared";

const LIMIT = 25;
const KINDS: WorkKind[] = [
  "assessment", "review", "disposition", "approval", "acceptance", "action", "verification", "trigger", "trigger_review",
];
const STATUSES: CampaignStatus[] = ["draft", "active", "in_review", "closed", "cancelled"];

/** 待办指向哪一页：风险与行动都在所属评估项的页面上处理，触发进触发详情。 */
function workLink(row: WorkRow): string {
  if (row.kind === "trigger" || row.kind === "trigger_review") return `/rcsa/triggers/${row.id}`;
  return row.item_id ? `/rcsa/items/${row.item_id}` : "/rcsa";
}

function MyWork() {
  const { t } = useTranslation();
  const work = useQuery({ queryKey: ["rcsa", "work"], queryFn: api.work });
  if (work.isPending) return <LoadingState />;
  if (work.error) return <ErrorNotice error={work.error} />;
  const rows = work.data.rows;
  if (rows.length === 0) return <EmptyState compact tone="success" title={t("rcsa.work.empty")} />;
  return (
    <div>
      {KINDS.filter((kind) => rows.some((row) => row.kind === kind)).map((kind) => (
        <section key={kind} className="rcsa-work-group" aria-label={t(`rcsa.work.kinds.${kind}`)}>
          <h4 className="kn-m-0">{t(`rcsa.work.kinds.${kind}`)}</h4>
          <ul className="kn-stack-1">
            {rows.filter((row) => row.kind === kind).map((row) => (
              <li key={`${row.kind}-${row.id}`}>
                <Link to={workLink(row)}>{row.title}</Link>
                {row.campaign_name ? <span className="kn-secondary"> · {row.campaign_name}</span> : null}{" "}
                <StatusBadge status={row.status} />{" "}
                {row.due_at ? (
                  <span className={row.overdue ? "rcsa-overdue" : "kn-secondary"}>
                    {row.overdue ? t("rcsa.overdueSince", { date: row.due_at }) : t("rcsa.dueOn", { date: row.due_at })}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function Campaigns() {
  const { t } = useTranslation();
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const campaigns = useQuery({
    queryKey: ["rcsa", "campaigns", status, offset],
    queryFn: () => api.campaigns(status, offset, LIMIT),
  });
  return (
    <Card
      title={t("rcsa.campaigns.title")}
      actions={
        <select
          aria-label={t("rcsa.campaigns.statusFilter")}
          value={status}
          onChange={(event) => { setStatus(event.target.value); setOffset(0); }}
        >
          <option value="">{t("rcsa.campaigns.allStatuses")}</option>
          {STATUSES.map((s) => <option key={s} value={s}>{t(`rcsa.status.${s}`)}</option>)}
        </select>
      }
    >
      {campaigns.isPending && <LoadingState />}
      <ErrorNotice error={campaigns.error} />
      {campaigns.data && campaigns.data.items.length === 0 && (
        <EmptyState compact title={status ? t("rcsa.campaigns.noneWithStatus") : t("rcsa.campaigns.none")} />
      )}
      {campaigns.data && campaigns.data.items.length > 0 && (
        <DataTable
          className="kn-m-0"
          head={
            <tr>
              <th>{t("rcsa.campaigns.name")}</th>
              <th>{t("rcsa.campaigns.period")}</th>
              <th>{t("rcsa.campaigns.status")}</th>
              <th>{t("rcsa.campaigns.progress")}</th>
            </tr>
          }
        >
          {campaigns.data.items.map((campaign) => (
            <tr key={campaign.id}>
              <td><Link to={`/rcsa/campaigns/${campaign.id}`}>{campaign.name}</Link></td>
              <td>{campaign.period_start} – {campaign.period_end}</td>
              <td><StatusBadge status={campaign.status} /></td>
              <td>
                {t("rcsa.campaigns.counts", {
                  approved: campaign.counts.approved,
                  excluded: campaign.counts.excluded,
                  total: campaign.counts.total,
                })}
              </td>
            </tr>
          ))}
        </DataTable>
      )}
      {campaigns.data && <Pager total={campaigns.data.total} offset={offset} limit={LIMIT} onChange={setOffset} />}
    </Card>
  );
}

export function RcsaHome() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const canManage = user?.role === "admin" || user?.role === "grc_lead";
  return (
    <section className="kn-page">
      <PageHeader
        title={t("rcsa.title")}
        subtitle={t("rcsa.subtitle")}
        actions={
          <div className="kn-row kn-row-gap">
            <Link className="kn-btn kn-btn-secondary" to="/rcsa/triggers">{t("rcsa.triggers.title")}</Link>
            <Link className="kn-btn kn-btn-secondary" to="/rcsa/profiles">{t("rcsa.profiles.title")}</Link>
            {canManage && <Link className="kn-btn kn-btn-primary" to="/rcsa/campaigns/new">{t("rcsa.campaigns.new")}</Link>}
          </div>
        }
      />
      <RcsaGate>
        <Card title={t("rcsa.work.title")}><MyWork /></Card>
        <Campaigns />
      </RcsaGate>
    </section>
  );
}
