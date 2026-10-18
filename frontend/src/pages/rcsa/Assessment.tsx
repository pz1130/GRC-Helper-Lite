// 评估页：左边冻结的控制与来源，中间作答、证据和风险情景，右边复核与版本历史。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { useAuth } from "../../auth";
import { Card, EmptyState, LoadingState, PageHeader } from "../../components/ui";
import { api, newKey, send, type Answer, type Effectiveness, type Item, type Response } from "./api";
import { AiPanel } from "./AiPanel";
import { EvidencePanel } from "./EvidencePanel";
import { RiskPanel } from "./RiskPanel";
import { ErrorNotice, PersonSelect, RcsaGate, StatusBadge, useUnsavedGuard, usePeople } from "./shared";

const RATINGS: Effectiveness[] = ["effective", "partially_effective", "ineffective", "not_assessed"];

const EMPTY: Answer = {
  design: { rating: null, rationale: "" },
  operating: { rating: null, rationale: "" },
  assessment_period_start: null,
  assessment_period_end: null,
  limitations: "",
  independent_test_status: null,
  independent_test_reason: "",
  not_assessed_reason: "",
  evidence_followup: null,
  no_risk_rationale: "",
  evidence_limitations_acknowledged: false,
};

function Frozen({ item }: { item: Item }) {
  const { t } = useTranslation();
  const control = item.control_snapshot;
  return (
    <Card title={t("rcsa.assessment.frozenControl")} className="rcsa-frozen">
      <p className="kn-m-0"><strong>{control?.code}</strong> {control?.title}</p>
      <p>{control?.statement}</p>
      <h4>{t("rcsa.assessment.mappings")}</h4>
      <ul>{item.mapping_snapshot?.map((m) => <li key={m.code}>{m.code} — {m.title} <span className="kn-secondary">({m.strength})</span></li>)}</ul>
      <h4>{t("rcsa.assessment.sources")}</h4>
      {item.source_snapshot?.length
        ? <ul>{item.source_snapshot.map((s) => <li key={`${s.document_id}-${s.citation_label}`}><Link to={`/documents/${s.document_id}`}>{s.citation_label}</Link></li>)}</ul>
        : <p className="kn-secondary">{t("rcsa.assessment.noSources")}</p>}
    </Card>
  );
}

function RatingField({ label, value, onChange, disabled }: {
  label: string; value: Answer["design"]; onChange: (v: Answer["design"]) => void; disabled: boolean;
}) {
  const { t } = useTranslation();
  return (
    <fieldset className="rcsa-form rcsa-wide" disabled={disabled}>
      <legend>{label}</legend>
      <label>{t("rcsa.assessment.rating")}
        <select value={value.rating ?? ""} onChange={(e) => onChange({ ...value, rating: (e.target.value || null) as Effectiveness | null })}>
          <option value="">{t("rcsa.assessment.notRated")}</option>
          {RATINGS.map((r) => <option key={r} value={r}>{t(`rcsa.effectiveness.${r}`)}</option>)}
        </select>
      </label>
      <label className="rcsa-wide">{t("rcsa.assessment.rationale")}
        <textarea rows={2} value={value.rationale} onChange={(e) => onChange({ ...value, rationale: e.target.value })} />
      </label>
    </fieldset>
  );
}

function AnswerForm({ response, editable, onChanged, savedVersion, onSaved }: {
  response: Response; editable: boolean; onChanged: () => void;
  // 保存成功后版本号变了，表单按 key 重建，自己的状态会丢；「已保存」由外层记住是哪一版。
  savedVersion: number | null; onSaved: (version: number) => void;
}) {
  const { t } = useTranslation();
  const { people } = usePeople();
  // 版本变了组件会按 key 重建；这里不跟着 response 重置，后台重新拉取不能冲掉没保存的修改。
  const [answer, setAnswer] = useState<Answer>({ ...EMPTY, ...response.answer });
  const dirty = useMemo(() => JSON.stringify(answer) !== JSON.stringify({ ...EMPTY, ...response.answer }), [answer, response]);
  useUnsavedGuard(editable && dirty);
  const save = useMutation({
    mutationFn: () => send<Response>(`/api/rcsa/responses/${response.id}`, "PATCH", { answer, expected_version: response.version }),
    onSuccess: (saved) => { onSaved(saved.version); onChanged(); },
  });
  const submit = useMutation({
    mutationFn: () => send<Response>(`/api/rcsa/responses/${response.id}/submit`, "POST", { expected_version: response.version }, newKey()),
    onSuccess: onChanged,
  });
  const set = (patch: Partial<Answer>) => setAnswer({ ...answer, ...patch });
  const notAssessed = answer.design.rating === "not_assessed" || answer.operating.rating === "not_assessed";
  return (
    <Card title={t("rcsa.assessment.answer", { revision: response.revision })} actions={<StatusBadge status={response.status} />}>
      <form className="rcsa-form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <RatingField label={t("rcsa.assessment.design")} value={answer.design} onChange={(design) => set({ design })} disabled={!editable} />
        <RatingField label={t("rcsa.assessment.operating")} value={answer.operating} onChange={(operating) => set({ operating })} disabled={!editable} />
        <fieldset className="rcsa-form rcsa-wide" disabled={!editable}>
          <legend>{t("rcsa.assessment.period")}</legend>
          <label>{t("rcsa.wizard.periodStart")}<input type="date" value={answer.assessment_period_start ?? ""} onChange={(e) => set({ assessment_period_start: e.target.value || null })} /></label>
          <label>{t("rcsa.wizard.periodEnd")}<input type="date" value={answer.assessment_period_end ?? ""} onChange={(e) => set({ assessment_period_end: e.target.value || null })} /></label>
        </fieldset>
        {notAssessed && (
          <fieldset className="rcsa-form rcsa-wide" disabled={!editable}>
            <legend>{t("rcsa.assessment.notAssessed")}</legend>
            <label className="rcsa-wide">{t("rcsa.assessment.gap")}<textarea rows={2} value={answer.not_assessed_reason} onChange={(e) => set({ not_assessed_reason: e.target.value })} /></label>
            <PersonSelect label={t("rcsa.assessment.followupOwner")} value={answer.evidence_followup?.owner_id ?? null} people={people}
              onChange={(owner) => set({ evidence_followup: owner ? { owner_id: owner, due_at: answer.evidence_followup?.due_at ?? "", description: answer.evidence_followup?.description ?? "" } : null })} />
            <label>{t("rcsa.assessment.followupDue")}<input type="date" value={answer.evidence_followup?.due_at ?? ""} disabled={!answer.evidence_followup}
              onChange={(e) => answer.evidence_followup && set({ evidence_followup: { ...answer.evidence_followup, due_at: e.target.value } })} /></label>
            <label className="rcsa-wide">{t("rcsa.assessment.followupWhat")}<input value={answer.evidence_followup?.description ?? ""} disabled={!answer.evidence_followup}
              onChange={(e) => answer.evidence_followup && set({ evidence_followup: { ...answer.evidence_followup, description: e.target.value } })} /></label>
          </fieldset>
        )}
        <fieldset className="rcsa-form rcsa-wide" disabled={!editable}>
          <legend>{t("rcsa.assessment.independentTest")}</legend>
          <label>{t("rcsa.assessment.testStatus")}
            <select value={answer.independent_test_status ?? ""} onChange={(e) => set({ independent_test_status: (e.target.value || null) as Answer["independent_test_status"] })}>
              <option value="">{t("rcsa.assessment.notRated")}</option>
              {(["provided", "not_provided", "not_applicable"] as const).map((s) => <option key={s} value={s}>{t(`rcsa.testStatus.${s}`)}</option>)}
            </select>
          </label>
          {answer.independent_test_status && answer.independent_test_status !== "provided" && (
            <label className="rcsa-wide">{t("rcsa.assessment.testReason")}<input value={answer.independent_test_reason} onChange={(e) => set({ independent_test_reason: e.target.value })} /></label>
          )}
        </fieldset>
        <label className="rcsa-wide">{t("rcsa.assessment.limitations")}
          <textarea rows={2} disabled={!editable} value={answer.limitations} onChange={(e) => set({ limitations: e.target.value })} />
        </label>
        <label className="kn-check-label rcsa-wide">
          <input type="checkbox" disabled={!editable} checked={answer.evidence_limitations_acknowledged} onChange={(e) => set({ evidence_limitations_acknowledged: e.target.checked })} />
          {t("rcsa.assessment.acknowledge")}
        </label>
        <label className="rcsa-wide">{t("rcsa.assessment.noRisk")}
          <input disabled={!editable} value={answer.no_risk_rationale} onChange={(e) => set({ no_risk_rationale: e.target.value })} />
        </label>
        {editable && (
          <div className="rcsa-actions rcsa-wide">
            <button type="submit" className="kn-btn kn-btn-secondary" disabled={!dirty || save.isPending}>{t("common.save")}</button>
            <button type="button" className="kn-btn kn-btn-primary" disabled={dirty || submit.isPending} onClick={() => submit.mutate()}>
              {t("rcsa.assessment.submit")}
            </button>
            {dirty ? <span className="kn-secondary kn-text-xs">{t("rcsa.assessment.saveFirst")}</span> : null}
            {savedVersion === response.version && !dirty ? <span role="status" className="kn-ok-text">{t("common.saved")}</span> : null}
          </div>
        )}
        <div className="rcsa-wide"><ErrorNotice error={save.error ?? submit.error} onReload={onChanged} /></div>
      </form>
    </Card>
  );
}

function ReviewPanel({ item, response, onChanged }: { item: Item; response: Response | undefined; onChanged: () => void }) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const review = useMutation({
    mutationFn: (decision: "approve" | "return") => send(`/api/rcsa/responses/${response!.id}/review`, "POST",
      { decision, reason, expected_version: response!.version }, newKey()),
    onSuccess: () => { setReason(""); onChanged(); },
  });
  const exclude = useMutation({
    mutationFn: () => send(`/api/rcsa/items/${item.id}/exclude`, "POST", { reason, expected_version: item.version }, newKey()),
    onSuccess: onChanged,
  });
  return (
    <Card title={t("rcsa.review.title")}>
      <label className="kn-block">{t("rcsa.review.reason")}
        <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <div className="rcsa-actions">
        {item.status === "submitted" && response && (
          <>
            <button type="button" className="kn-btn kn-btn-primary" disabled={review.isPending} onClick={() => review.mutate("approve")}>{t("rcsa.review.approve", { revision: response.revision })}</button>
            <button type="button" className="kn-btn kn-btn-secondary" disabled={review.isPending || !reason.trim()} onClick={() => review.mutate("return")}>{t("rcsa.review.return")}</button>
          </>
        )}
        {item.status !== "approved" && item.status !== "excluded" && (
          <button type="button" className="kn-btn kn-btn-danger" disabled={exclude.isPending || !reason.trim()} onClick={() => exclude.mutate()}>{t("rcsa.review.exclude")}</button>
        )}
      </div>
      <p className="kn-secondary kn-text-xs">{t("rcsa.review.meaning")}</p>
      <ErrorNotice error={review.error ?? exclude.error} onReload={onChanged} />
    </Card>
  );
}

function History({ itemId }: { itemId: number }) {
  const { t } = useTranslation();
  const { nameOf } = usePeople();
  const responses = useQuery({ queryKey: ["rcsa", "item", itemId, "responses"], queryFn: () => api.responses(itemId) });
  const reviews = useQuery({ queryKey: ["rcsa", "item", itemId, "reviews"], queryFn: () => api.reviews(itemId) });
  return (
    <Card title={t("rcsa.history.title")}>
      <ul className="kn-stack-1">
        {responses.data?.map((r) => (
          <li key={r.id}>{t("rcsa.history.revision", { revision: r.revision })} <StatusBadge status={r.status} />
            {r.submitted_at ? <span className="kn-secondary"> · {r.submitted_at.slice(0, 10)}</span> : null}</li>
        ))}
      </ul>
      <h5>{t("rcsa.history.reviews")}</h5>
      {reviews.data?.length ? (
        <ul className="kn-stack-1">
          {reviews.data.map((r) => (
            <li key={r.id}>{t(`rcsa.decisions.${r.decision}`)} · {nameOf(r.reviewer_id)}
              {r.reason ? <div className="kn-secondary kn-text-xs">{r.reason}</div> : null}</li>
          ))}
        </ul>
      ) : <p className="kn-secondary kn-text-xs">{t("rcsa.history.noReviews")}</p>}
    </Card>
  );
}

export function Assessment() {
  const { t } = useTranslation();
  const id = Number(useParams().id);
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const item = useQuery({ queryKey: ["rcsa", "item", id], queryFn: () => api.item(id) });
  const campaign = useQuery({
    queryKey: ["rcsa", "campaign", item.data?.campaign_id],
    queryFn: () => api.campaign(item.data!.campaign_id),
    enabled: Boolean(item.data),
  });
  const responses = useQuery({ queryKey: ["rcsa", "item", id, "responses"], queryFn: () => api.responses(id) });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["rcsa", "item", id] });
    void queryClient.invalidateQueries({ queryKey: ["rcsa", "work"] });
  };
  const open = useMutation({
    mutationFn: () => send(`/api/rcsa/items/${id}/responses`, "POST", { expected_version: item.data!.version }),
    onSuccess: refresh,
  });
  if (item.isPending || campaign.isPending) return <section className="kn-page"><LoadingState /></section>;
  if (item.error) return <section className="kn-page"><ErrorNotice error={item.error} /></section>;
  const current = responses.data?.find((r) => r.id === item.data.current_response_id);
  const active = campaign.data?.status === "active";
  const isAssignee = user?.id === item.data.assignee_id;
  const isReviewer = user?.id === item.data.reviewer_id;
  const editable = active && isAssignee && current?.status === "draft";
  const canStart = active && isAssignee && (item.data.status === "pending" || item.data.status === "returned");
  const riskEditable = active && isAssignee && ["pending", "draft", "returned", "approved"].includes(item.data.status);

  return (
    <section className="kn-page">
      <RcsaGate>
        <PageHeader
          title={`${item.data.control_snapshot?.code ?? ""} ${item.data.control_snapshot?.title ?? ""}`}
          badge={<StatusBadge status={item.data.status} />}
          subtitle={campaign.data ? <Link to={`/rcsa/campaigns/${campaign.data.id}`}>{campaign.data.name}</Link> : undefined}
        />
        <div className="rcsa-assessment">
          <Frozen item={item.data} />
          <div className="kn-stack-3">
            {canStart && (
              <Card>
                <button type="button" className="kn-btn kn-btn-primary" disabled={open.isPending} onClick={() => open.mutate()}>
                  {item.data.status === "returned" ? t("rcsa.assessment.startRevision") : t("rcsa.assessment.start")}
                </button>
                <ErrorNotice error={open.error} onReload={refresh} />
              </Card>
            )}
            {current ? (
              <>
                <AnswerForm key={`${current.id}-${current.version}`} response={current} editable={editable} onChanged={refresh}
                  savedVersion={savedVersion} onSaved={setSavedVersion} />
                <EvidencePanel response={current} controlId={item.data.control_id} editable={editable} onChanged={refresh} />
                <AiPanel response={current} editable={editable} canRank={active && (isAssignee || isReviewer)} onChanged={refresh} />
              </>
            ) : <EmptyState compact title={t("rcsa.assessment.notStarted")} />}
            <RiskPanel item={item.data} canEdit={riskEditable} />
          </div>
          <div className="kn-stack-3">
            {active && isReviewer && <ReviewPanel item={item.data} response={current} onChanged={refresh} />}
            <History itemId={id} />
          </div>
        </div>
      </RcsaGate>
    </section>
  );
}
