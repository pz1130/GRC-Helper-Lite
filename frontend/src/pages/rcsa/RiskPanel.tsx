// 风险情景、处置、整改行动与发布到登记册。分值由服务端计算，这里只显示。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useAuth } from "../../auth";
import { Card, EmptyState } from "../../components/ui";
import { api, newKey, send, type Action, type Disposition, type Item, type Risk } from "./api";
import { RiskTemplatePicker } from "./ItGuidance";
import { ErrorNotice, PersonSelect, StatusBadge, usePeople } from "./shared";

const SCORES = [1, 2, 3, 4, 5];
const STRATEGIES: Disposition[] = ["accept", "reduce", "transfer", "avoid", "terminate"];
// 每种策略要填的说明字段（键名与后端 _disposition_problems 一致）。
const DETAILS: Record<Disposition, string[]> = {
  accept: ["rationale", "conditions"],
  reduce: [],
  transfer: ["counterparty", "basis_ref", "retained_risk"],
  avoid: ["decision", "scope", "responsible", "basis_ref"],
  terminate: ["scope", "completion_criteria", "responsible", "basis_ref"],
};

function ScoreSelect({ label, value, onChange }: { label: string; value: number | null; onChange: (v: number | null) => void }) {
  const { t } = useTranslation();
  return (
    <label>{label}
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="">{t("rcsa.risks.unrated")}</option>
        {SCORES.map((score) => <option key={score} value={score}>{score}</option>)}
      </select>
    </label>
  );
}

function ScenarioForm({ item, risk, onDone }: { item: Item; risk?: Risk; onDone: () => void }) {
  const { t } = useTranslation();
  const { people } = usePeople();
  const { user } = useAuth();
  const [key, setKey] = useState(risk?.scenario_key ?? "");
  const [cause, setCause] = useState(risk?.cause ?? "");
  const [event, setEvent] = useState(risk?.event ?? "");
  const [impact, setImpact] = useState(risk?.impact ?? "");
  const [il, setIl] = useState<number | null>(risk?.inherent_likelihood ?? null);
  const [ii, setIi] = useState<number | null>(risk?.inherent_impact ?? null);
  const [irat, setIrat] = useState(risk?.inherent_rationale ?? "");
  const [rl, setRl] = useState<number | null>(risk?.residual_likelihood ?? null);
  const [ri, setRi] = useState<number | null>(risk?.residual_impact ?? null);
  const [rrat, setRrat] = useState(risk?.residual_rationale ?? "");
  const [owner, setOwner] = useState<number | null>(risk?.owner_id ?? user?.id ?? null);
  const save = useMutation({
    mutationFn: () => {
      const body = {
        cause, event, impact, inherent_likelihood: il, inherent_impact: ii, inherent_rationale: irat,
        residual_likelihood: rl, residual_impact: ri, residual_rationale: rrat,
      };
      return risk
        ? send(`/api/rcsa/item-risks/${risk.id}`, "PATCH", { ...body, expected_version: risk.version })
        : send(`/api/rcsa/items/${item.id}/risks`, "POST", { ...body, scenario_key: key, owner_id: owner });
    },
    onSuccess: onDone,
  });
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(); };
  return (
    <form className="rcsa-form" onSubmit={submit} aria-label={risk ? t("rcsa.risks.edit") : t("rcsa.risks.add")}>
      {!risk && <RiskTemplatePicker onApply={(template) => {
        if (!key.trim()) setKey(template.id);
        if (!cause.trim()) setCause(template.cause);
        if (!event.trim()) setEvent(template.event);
        if (!impact.trim()) setImpact(template.impact);
      }} />}
      {!risk && <label>{t("rcsa.risks.key")}<input required pattern="[a-z0-9][a-z0-9._\-]*" value={key} onChange={(e) => setKey(e.target.value)} /></label>}
      {!risk && <PersonSelect label={t("rcsa.risks.owner")} value={owner} onChange={setOwner} people={people} allowEmpty={false} />}
      <label className="rcsa-wide">{t("rcsa.risks.cause")}<input required value={cause} onChange={(e) => setCause(e.target.value)} /></label>
      <label className="rcsa-wide">{t("rcsa.risks.event")}<input required value={event} onChange={(e) => setEvent(e.target.value)} /></label>
      <label className="rcsa-wide">{t("rcsa.risks.impact")}<input required value={impact} onChange={(e) => setImpact(e.target.value)} /></label>
      <ScoreSelect label={t("rcsa.risks.inherentL")} value={il} onChange={setIl} />
      <ScoreSelect label={t("rcsa.risks.inherentI")} value={ii} onChange={setIi} />
      <label className="rcsa-wide">{t("rcsa.risks.inherentRationale")}<input value={irat} onChange={(e) => setIrat(e.target.value)} /></label>
      <ScoreSelect label={t("rcsa.risks.residualL")} value={rl} onChange={setRl} />
      <ScoreSelect label={t("rcsa.risks.residualI")} value={ri} onChange={setRi} />
      <label className="rcsa-wide">{t("rcsa.risks.residualRationale")}<input value={rrat} onChange={(e) => setRrat(e.target.value)} /></label>
      <div className="rcsa-actions rcsa-wide">
        <button type="submit" className="kn-btn kn-btn-secondary" disabled={!il || !ii || (rl === null) !== (ri === null) || save.isPending}>
          {risk ? t("common.save") : t("rcsa.risks.add")}
        </button>
      </div>
      <div className="rcsa-wide"><ErrorNotice error={save.error} onReload={onDone} /></div>
    </form>
  );
}

function DispositionForm({ risk, onDone }: { risk: Risk; onDone: () => void }) {
  const { t } = useTranslation();
  const [strategy, setStrategy] = useState<Disposition>(risk.disposition ?? "reduce");
  const [details, setDetails] = useState<Record<string, string | boolean>>(risk.disposition_details ?? {});
  const [until, setUntil] = useState(risk.accept_until ?? "");
  const [reassess, setReassess] = useState(risk.reassess_at ?? "");
  const propose = useMutation({
    mutationFn: () => send(`/api/rcsa/item-risks/${risk.id}/disposition`, "POST", {
      disposition: strategy, details, expected_version: risk.version,
      ...(strategy === "accept" ? { accept_until: until || null, reassess_at: reassess || null } : {}),
    }),
    onSuccess: onDone,
  });
  return (
    <div className="rcsa-form">
      <label>{t("rcsa.risks.strategy")}
        <select value={strategy} onChange={(e) => setStrategy(e.target.value as Disposition)}>
          {STRATEGIES.map((s) => <option key={s} value={s}>{t(`rcsa.risks.strategies.${s}`)}</option>)}
        </select>
      </label>
      {DETAILS[strategy].map((field) => (
        <label key={field}>{t(`rcsa.risks.details.${field}`)}
          <input value={String(details[field] ?? "")} onChange={(e) => setDetails({ ...details, [field]: e.target.value })} />
        </label>
      ))}
      {strategy === "accept" && (
        <>
          <label>{t("rcsa.risks.acceptUntil")}<input type="date" value={until} onChange={(e) => setUntil(e.target.value)} /></label>
          <label>{t("rcsa.risks.reassessAt")}<input type="date" value={reassess} onChange={(e) => setReassess(e.target.value)} /></label>
        </>
      )}
      {["transfer", "avoid", "terminate"].includes(strategy) && (
        <label className="kn-check-label">
          <input type="checkbox" checked={Boolean(details.effective)} onChange={(e) => setDetails({ ...details, effective: e.target.checked })} />
          {t("rcsa.risks.details.effective")}
        </label>
      )}
      <div className="rcsa-actions rcsa-wide">
        <button type="button" className="kn-btn kn-btn-secondary" disabled={propose.isPending} onClick={() => propose.mutate()}>
          {t("rcsa.risks.propose")}
        </button>
      </div>
      <div className="rcsa-wide"><ErrorNotice error={propose.error} onReload={onDone} /></div>
    </div>
  );
}

function DispositionDecision({ risk, onDone }: { risk: Risk; onDone: () => void }) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const decide = useMutation({
    mutationFn: (decision: "approve" | "return") => send(`/api/rcsa/item-risks/${risk.id}/review-disposition`, "POST",
      { decision, reason, expected_version: risk.version }, newKey()),
    onSuccess: onDone,
  });
  return (
    <div className="rcsa-form">
      <label className="rcsa-wide">{t("rcsa.review.reason")}<input value={reason} onChange={(e) => setReason(e.target.value)} /></label>
      <div className="rcsa-actions rcsa-wide">
        <button type="button" className="kn-btn kn-btn-primary" disabled={decide.isPending} onClick={() => decide.mutate("approve")}>{t("rcsa.risks.approveDisposition")}</button>
        <button type="button" className="kn-btn kn-btn-secondary" disabled={decide.isPending || !reason.trim()} onClick={() => decide.mutate("return")}>{t("rcsa.risks.returnDisposition")}</button>
      </div>
      <div className="rcsa-wide"><ErrorNotice error={decide.error} onReload={onDone} /></div>
    </div>
  );
}

function ActionRow({ action, onDone }: { action: Action; onDone: () => void }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { nameOf } = usePeople();
  const [evidence, setEvidence] = useState("");
  const [result, setResult] = useState("");
  const [reason, setReason] = useState("");
  const update = useMutation({
    mutationFn: (status: string) => send(`/api/rcsa/actions/${action.id}`, "PATCH", {
      status, expected_version: action.version,
      ...(evidence.trim() ? { implementation_evidence: [...action.implementation_evidence, { description: evidence.trim(), location: "" }] } : {}),
    }),
    onSuccess: () => { setEvidence(""); onDone(); },
  });
  const verify = useMutation({
    mutationFn: (decision: "pass" | "fail") => send(`/api/rcsa/actions/${action.id}/verify`, "POST",
      { decision, result, expected_version: action.version }, newKey()),
    onSuccess: onDone,
  });
  const cancel = useMutation({
    mutationFn: () => send(`/api/rcsa/actions/${action.id}/cancel`, "POST", { reason, expected_version: action.version }),
    onSuccess: onDone,
  });
  const live = action.status !== "verified" && action.status !== "cancelled";
  const isOwner = user?.id === action.owner_id;
  const isVerifier = user?.id === action.verifier_id;
  return (
    <li className="kn-stack-1">
      <div>
        <strong>{action.title}</strong> <StatusBadge status={action.status} />{" "}
        <span className="kn-secondary">{t("rcsa.actions.who", { owner: nameOf(action.owner_id), verifier: nameOf(action.verifier_id), date: action.due_at })}</span>
      </div>
      <div className="kn-secondary kn-text-xs">{t("rcsa.actions.criteria")}: {action.success_criteria}</div>
      {action.verification_result && <div className="kn-text-xs">{t("rcsa.actions.result")}: {action.verification_result}</div>}
      {live && isOwner && action.status !== "pending_verification" && (
        <div className="rcsa-form">
          <label className="rcsa-wide">{t("rcsa.actions.evidence")}<input value={evidence} onChange={(e) => setEvidence(e.target.value)} /></label>
          <div className="rcsa-actions rcsa-wide">
            {action.status === "open" && <button type="button" className="kn-btn-sm" onClick={() => update.mutate("in_progress")}>{t("rcsa.actions.start")}</button>}
            <button type="button" className="kn-btn-sm" disabled={update.isPending} onClick={() => update.mutate("pending_verification")}>{t("rcsa.actions.askVerification")}</button>
          </div>
        </div>
      )}
      {action.status === "pending_verification" && isVerifier && (
        <div className="rcsa-form">
          <label className="rcsa-wide">{t("rcsa.actions.result")}<input value={result} onChange={(e) => setResult(e.target.value)} /></label>
          <div className="rcsa-actions rcsa-wide">
            <button type="button" className="kn-btn-sm" disabled={!result.trim() || verify.isPending} onClick={() => verify.mutate("pass")}>{t("rcsa.actions.pass")}</button>
            <button type="button" className="kn-btn-sm" disabled={!result.trim() || verify.isPending} onClick={() => verify.mutate("fail")}>{t("rcsa.actions.fail")}</button>
          </div>
        </div>
      )}
      {live && !isOwner && (user?.role === "admin" || user?.role === "grc_lead") && (
        <div className="rcsa-actions">
          <input aria-label={t("rcsa.actions.cancelReason")} placeholder={t("rcsa.actions.cancelReason")} value={reason} onChange={(e) => setReason(e.target.value)} />
          <button type="button" className="kn-btn-sm kn-btn-danger" disabled={!reason.trim() || cancel.isPending} onClick={() => cancel.mutate()}>{t("rcsa.actions.cancel")}</button>
        </div>
      )}
      <ErrorNotice error={update.error ?? verify.error ?? cancel.error} onReload={onDone} />
    </li>
  );
}

function ActionForm({ risk, onDone }: { risk: Risk; onDone: () => void }) {
  const { t } = useTranslation();
  const { people } = usePeople();
  const [title, setTitle] = useState("");
  const [rootCause, setRootCause] = useState("");
  const [measure, setMeasure] = useState("");
  const [owner, setOwner] = useState<number | null>(null);
  const [verifier, setVerifier] = useState<number | null>(null);
  const [due, setDue] = useState("");
  const [criteria, setCriteria] = useState("");
  const add = useMutation({
    mutationFn: () => send(`/api/rcsa/item-risks/${risk.id}/actions`, "POST", {
      title, root_cause: rootCause, measure, owner_id: owner, verifier_id: verifier, due_at: due, success_criteria: criteria,
    }),
    onSuccess: () => { setTitle(""); setCriteria(""); onDone(); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); add.mutate(); };
  return (
    <form className="rcsa-form" onSubmit={submit} aria-label={t("rcsa.actions.add")}>
      <label className="rcsa-wide">{t("rcsa.actions.title")}<input required value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <label>{t("rcsa.actions.rootCause")}<input value={rootCause} onChange={(e) => setRootCause(e.target.value)} /></label>
      <label>{t("rcsa.actions.measure")}<input value={measure} onChange={(e) => setMeasure(e.target.value)} /></label>
      <PersonSelect label={t("rcsa.actions.owner")} value={owner} onChange={setOwner} people={people} filter={(p) => p.can_respond} />
      <PersonSelect label={t("rcsa.actions.verifier")} value={verifier} onChange={setVerifier} people={people} filter={(p) => p.can_review && p.id !== owner} />
      <label>{t("rcsa.actions.due")}<input type="date" required value={due} onChange={(e) => setDue(e.target.value)} /></label>
      <label className="rcsa-wide">{t("rcsa.actions.criteria")}<input required value={criteria} onChange={(e) => setCriteria(e.target.value)} /></label>
      <div className="rcsa-actions rcsa-wide">
        <button type="submit" className="kn-btn kn-btn-secondary" disabled={!owner || !verifier || add.isPending}>{t("rcsa.actions.add")}</button>
      </div>
      <div className="rcsa-wide"><ErrorNotice error={add.error} /></div>
    </form>
  );
}

function RiskCard({ risk, item, canEditScenario, onDone }: { risk: Risk; item: Item; canEditScenario: boolean; onDone: () => void }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { nameOf } = usePeople();
  const [editing, setEditing] = useState(false);
  const actions = useQuery({ queryKey: ["rcsa", "item", item.id, "actions", risk.id], queryFn: () => api.actions(risk.id) });
  const publish = useMutation({
    mutationFn: () => send(`/api/rcsa/item-risks/${risk.id}/publish`, "POST", { mode: "create", expected_version: risk.version }, newKey()),
    onSuccess: onDone,
  });
  const approved = item.status === "approved";
  const canPropose = approved && (user?.id === item.assignee_id || user?.id === risk.owner_id) && risk.disposition_status !== "proposed";
  const canDecide = risk.disposition_status === "proposed" && risk.disposition_proposed_by !== user?.id
    && (user?.role === "admin" || user?.role === "grc_lead");
  const manager = user?.role === "admin" || user?.role === "grc_lead";
  return (
    <li className="kn-card kn-stack-2" aria-label={risk.scenario_key}>
      <div>
        <strong>{risk.event}</strong> <span className="kn-secondary">({risk.scenario_key})</span>
        <div className="kn-text-xs kn-secondary">{t("rcsa.risks.chain", { cause: risk.cause, impact: risk.impact })}</div>
        <div>
          {t("rcsa.risks.inherentScore", { l: risk.inherent_likelihood, i: risk.inherent_impact, score: risk.inherent_score })}
          {" · "}
          {risk.residual_score === null ? t("rcsa.risks.residualMissing")
            : t("rcsa.risks.residualScore", { l: risk.residual_likelihood, i: risk.residual_impact, score: risk.residual_score })}
          {" · "}{t("rcsa.risks.ownedBy", { name: nameOf(risk.owner_id) })}
        </div>
        <div>
          {risk.disposition ? t(`rcsa.risks.strategies.${risk.disposition}`) : t("rcsa.risks.noDisposition")}{" "}
          <StatusBadge status={risk.disposition_status} />
          {risk.accept_until ? <span className="kn-secondary"> · {t("rcsa.risks.until", { date: risk.accept_until })}</span> : null}
          {risk.register_risk_id ? <span className="kn-secondary"> · {t("rcsa.risks.published", { id: risk.register_risk_id })}</span> : null}
        </div>
      </div>
      {canEditScenario && (editing
        ? <ScenarioForm item={item} risk={risk} onDone={() => { setEditing(false); onDone(); }} />
        : <div><button type="button" className="kn-btn-sm" onClick={() => setEditing(true)}>{t("rcsa.risks.edit")}</button>
          {approved && <span className="kn-secondary kn-text-xs"> {t("rcsa.risks.editReopens")}</span>}</div>)}
      {canPropose && <DispositionForm risk={risk} onDone={onDone} />}
      {canDecide && <DispositionDecision risk={risk} onDone={onDone} />}
      <div>
        <h5 className="kn-m-0">{t("rcsa.actions.heading")}</h5>
        {actions.data && actions.data.length > 0
          ? <ul className="kn-stack-2">{actions.data.map((a) => <ActionRow key={`${a.id}-${a.version}`} action={a} onDone={() => { void actions.refetch(); onDone(); }} />)}</ul>
          : <p className="kn-secondary kn-text-xs">{t("rcsa.actions.none")}</p>}
        {approved && (user?.id === item.assignee_id || user?.id === risk.owner_id || manager) && (
          <ActionForm risk={risk} onDone={() => { void actions.refetch(); onDone(); }} />
        )}
      </div>
      {approved && manager && !risk.register_risk_id && (
        <div className="rcsa-actions">
          <button type="button" className="kn-btn-sm" disabled={publish.isPending} onClick={() => publish.mutate()}>{t("rcsa.risks.publish")}</button>
        </div>
      )}
      <ErrorNotice error={publish.error} onReload={onDone} />
    </li>
  );
}

export function RiskPanel({ item, canEdit }: { item: Item; canEdit: boolean }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const risks = useQuery({ queryKey: ["rcsa", "item", item.id, "risks"], queryFn: () => api.risks(item.id) });
  const [adding, setAdding] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["rcsa", "item", item.id] });
  return (
    <Card title={t("rcsa.risks.title")}>
      <p className="kn-secondary kn-text-xs">{t("rcsa.risks.hint")}</p>
      {risks.data && risks.data.length === 0 && <EmptyState compact title={t("rcsa.risks.none")} />}
      <ul className="kn-stack-3">
        {risks.data?.map((risk) => (
          <RiskCard key={`${risk.id}-${risk.version}`} risk={risk} item={item} canEditScenario={canEdit} onDone={refresh} />
        ))}
      </ul>
      {canEdit && (adding
        ? <ScenarioForm item={item} onDone={() => { setAdding(false); void refresh(); }} />
        : <button type="button" className="kn-btn kn-btn-secondary" onClick={() => setAdding(true)}>{t("rcsa.risks.add")}</button>)}
    </Card>
  );
}
