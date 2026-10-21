// 内容库只提供作答素材；明确选用后才填空，不改变评分、负责人或正式控制库。
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type ItGuide, type RiskTemplate } from "./api";
import { ErrorNotice } from "./shared";

function Guide({ guide }: { guide: ItGuide }) {
  const { t } = useTranslation();
  return <div className="kn-stack-1">
    <strong>{guide.title}</strong>
    <p><strong>{t("rcsa.guidance.evidence")}: </strong>{guide.expected_evidence}</p>
    <p><strong>{t("rcsa.guidance.test")}: </strong>{guide.test_procedure}</p>
  </div>;
}

export function RiskTemplatePicker({ onApply }: { onApply: (template: RiskTemplate) => void }) {
  const { t } = useTranslation();
  const catalog = useQuery({ queryKey: ["rcsa", "it-catalog"], queryFn: api.itCatalog });
  const [selected, setSelected] = useState("");
  const template = catalog.data?.risks.find((r) => r.id === selected);
  return <details className="rcsa-wide">
    <summary>{t("rcsa.guidance.templates")}</summary>
    <p className="kn-secondary">{t("rcsa.guidance.hint")}</p>
    <label>{t("rcsa.guidance.chooseRisk")}
      <select value={selected} onChange={(e) => setSelected(e.target.value)}>
        <option value="">{t("rcsa.guidance.choose")}</option>
        {catalog.data?.risks.map((r) => <option key={r.id} value={r.id}>{r.domain} — {r.title}</option>)}
      </select>
    </label>
    {template && <>
      <p>{template.cause}</p><p>{template.event}</p><p>{template.impact}</p>
      <button type="button" className="kn-btn kn-btn-secondary" onClick={() => onApply(template)}>{t("rcsa.guidance.apply")}</button>
      {catalog.data?.guides.filter((g) => template.guide_ids.includes(g.id)).map((g) => <Guide key={g.id} guide={g} />)}
    </>}
    <ErrorNotice error={catalog.error} />
  </details>;
}

export function GuidePicker({ selected, onChange, editable }: {
  selected: string[]; onChange: (ids: string[]) => void; editable: boolean;
}) {
  const { t } = useTranslation();
  const catalog = useQuery({ queryKey: ["rcsa", "it-catalog"], queryFn: api.itCatalog });
  const [query, setQuery] = useState("");
  const guides = catalog.data?.guides ?? [];
  return <details className="rcsa-wide">
    <summary>{t("rcsa.guidance.title", { count: selected.length })}</summary>
    <p className="kn-secondary">{t("rcsa.guidance.hint")}</p>
    {editable && <label>{t("rcsa.guidance.search")}<input value={query} onChange={(e) => setQuery(e.target.value)} /></label>}
    {guides.filter((g) => editable ? `${g.title} ${g.id}`.toLowerCase().includes(query.toLowerCase()) : selected.includes(g.id)).map((g) => (
      <div key={g.id}>
        {editable && <label className="kn-check-label"><input type="checkbox" checked={selected.includes(g.id)}
          onChange={(e) => onChange(e.target.checked ? [...selected, g.id] : selected.filter((id) => id !== g.id))} />{g.title}</label>}
        {selected.includes(g.id) && <Guide guide={g} />}
      </div>
    ))}
    <ErrorNotice error={catalog.error} />
  </details>;
}
