// 信号随作答一起保存和冻结；复核提示由服务端根据本期范围计算。
import { useTranslation } from "react-i18next";
import type { AdverseSignal } from "./api";

const KINDS: AdverseSignal["kind"][] = ["overdue_vulnerability", "major_incident", "failed_change", "red_kri", "open_finding"];

export function Signals({ signals, onChange, editable, flagged = [] }: {
  signals: AdverseSignal[]; onChange: (signals: AdverseSignal[]) => void; editable: boolean; flagged?: number[];
}) {
  const { t } = useTranslation();
  const update = (index: number, patch: Partial<AdverseSignal>) => onChange(signals.map((s, i) => i === index ? { ...s, ...patch } : s));
  return <fieldset className="rcsa-form rcsa-wide" disabled={!editable}>
    <legend>{t("rcsa.signals.title")}</legend>
    <p className="rcsa-wide kn-secondary">{t("rcsa.signals.hint")}</p>
    {flagged.length > 0 && <p role="status" className="rcsa-wide rcsa-overdue">{t("rcsa.signals.warning")}</p>}
    {signals.map((signal, index) => <fieldset key={index} className="rcsa-form rcsa-wide">
      <legend>{t("rcsa.signals.number", { number: index + 1 })}</legend>
      <label>{t("rcsa.signals.kind")}<select value={signal.kind} onChange={(e) => update(index, { kind: e.target.value as AdverseSignal["kind"] })}>
        {KINDS.map((kind) => <option key={kind} value={kind}>{t(`rcsa.signals.kinds.${kind}`)}</option>)}
      </select></label>
      <label>{t("rcsa.signals.source")}<input required maxLength={500} value={signal.source_ref} onChange={(e) => update(index, { source_ref: e.target.value })} /></label>
      <label className="rcsa-wide">{t("rcsa.signals.description")}<textarea required maxLength={2000} value={signal.description} onChange={(e) => update(index, { description: e.target.value })} /></label>
      <label>{t("rcsa.signals.start")}<input required type="date" value={signal.started_on} onChange={(e) => update(index, { started_on: e.target.value })} /></label>
      <label>{t("rcsa.signals.end")}<input type="date" min={signal.started_on} value={signal.ended_on ?? ""} onChange={(e) => update(index, { ended_on: e.target.value || null })} /></label>
      <label className="rcsa-wide">{t("rcsa.signals.explanation")}<textarea maxLength={5000} value={signal.explanation} onChange={(e) => update(index, { explanation: e.target.value })} /></label>
      {editable && <button type="button" className="kn-btn kn-btn-secondary" onClick={() => onChange(signals.filter((_, i) => i !== index))}>{t("rcsa.signals.remove")}</button>}
    </fieldset>)}
    {editable && <button type="button" className="kn-btn kn-btn-secondary" disabled={signals.length >= 50} onClick={() => onChange([...signals, {
      kind: "major_incident", source_ref: "", description: "", started_on: "", ended_on: null, explanation: "",
    }])}>{t("rcsa.signals.add")}</button>}
    {!signals.length && <p className="rcsa-wide kn-secondary">{t("rcsa.signals.empty")}</p>}
  </fieldset>;
}
