// 可选 AI 辅助：两个通道都没启用时整块不显示，人工流程不受影响。
// 发起前先说清会发哪些输入、发给哪个服务；结果一律标 Suggested，只能由作答人逐项应用。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge, Card } from "../../components/ui";
import { api, newKey, send, type AiChannel, type Ranking, type Response, type Suggestion } from "./api";
import { ErrorNotice } from "./shared";

const FIELDS = ["design", "operating", "limitations"] as const;
type Field = (typeof FIELDS)[number];

function Outbound({ channel }: { channel: AiChannel }) {
  const { t } = useTranslation();
  return (
    <p className="kn-secondary kn-text-xs kn-m-0">
      {t("rcsa.ai.outbound", {
        inputs: channel.allowed_input_types.map((type) => t(`rcsa.ai.inputs.${type}`)).join(", "),
        service: channel.provider ? `${channel.provider.name} (${channel.provider.model})` : "—",
      })}
    </p>
  );
}

function SuggestionCard({ suggestion, response, editable, onApplied }: {
  suggestion: Suggestion; response: Response; editable: boolean; onApplied: () => void;
}) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<Field[]>([]);
  const apply = useMutation({
    mutationFn: () => send(`/api/rcsa/suggestions/${suggestion.id}/apply`, "POST",
      { fields: picked, expected_version: response.version }),
    onSuccess: onApplied,
  });
  const usable = editable && !suggestion.stale && suggestion.status === "pending";
  const s = suggestion.suggestions;
  return (
    <li className="kn-stack-1" aria-label={t("rcsa.ai.suggestionLabel", { id: suggestion.id })}>
      <div>
        <Badge tone="purple">{t("rcsa.ai.suggested")}</Badge>{" "}
        <span className="kn-secondary kn-text-xs">
          {t("rcsa.ai.generated", { date: suggestion.created_at.slice(0, 16).replace("T", " "), model: suggestion.model ?? "—" })}
        </span>{" "}
        {suggestion.stale && <Badge tone="amber">{t("rcsa.ai.stale")}</Badge>}
        {suggestion.status !== "pending" && <Badge tone="emerald">{t("rcsa.ai.applied")}</Badge>}
      </div>
      {FIELDS.map((field) => (
        <label key={field} className="kn-check-label kn-block">
          {usable && (
            <input type="checkbox" checked={picked.includes(field)}
              onChange={(e) => setPicked(e.target.checked ? [...picked, field] : picked.filter((f) => f !== field))} />
          )}
          <strong>{t(`rcsa.ai.fields.${field}`)}:</strong>{" "}
          {field === "limitations" ? s.limitations || "—" : (
            <>
              {s[field].rating ? t(`rcsa.effectiveness.${s[field].rating}`) : t("rcsa.assessment.notRated")} — {s[field].rationale}
              <span className="kn-secondary kn-text-xs"> [{s[field].citations.join(", ")}]</span>
            </>
          )}
        </label>
      ))}
      {s.unknowns.length > 0 && (
        <div className="kn-secondary kn-text-xs">{t("rcsa.ai.unknowns")}: {s.unknowns.join("; ")}</div>
      )}
      {usable && (
        <div className="rcsa-actions">
          <button type="button" className="kn-btn-sm" disabled={!picked.length || apply.isPending} onClick={() => apply.mutate()}>
            {t("rcsa.ai.apply")}
          </button>
        </div>
      )}
      <ErrorNotice error={apply.error} onReload={onApplied} />
    </li>
  );
}

function RankingResult({ ranking }: { ranking: Ranking }) {
  const { t } = useTranslation();
  return (
    <div className="kn-stack-1">
      {!ranking.ranked && <p role="alert" className="rcsa-warning kn-m-0">{t("rcsa.ai.rankingUnavailable")}</p>}
      {ranking.ranked && <Badge tone="purple">{t("rcsa.ai.suggested")}</Badge>}
      <ol>
        {ranking.candidates.map((candidate) => (
          <li key={candidate.evidence_item_id}>
            {candidate.title}
            {candidate.probability !== null && (
              <span className="kn-secondary"> — {t("rcsa.ai.relevance", { score: candidate.probability.toFixed(2) })}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

export function AiPanel({ response, editable, canRank, onChanged }: {
  response: Response; editable: boolean; canRank: boolean; onChanged: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["rcsa", "ai"], queryFn: api.ai, staleTime: 60_000 });
  const suggestions = useQuery({
    queryKey: ["rcsa", "suggestions", response.id],
    queryFn: () => api.suggestions(response.id),
    enabled: Boolean(status.data?.prefill.enabled),
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["rcsa", "suggestions", response.id] });
    onChanged();
  };
  const prefill = useMutation({
    mutationFn: () => send<Suggestion>(`/api/rcsa/responses/${response.id}/prefill`, "POST", {}, newKey()),
    onSuccess: refresh,
  });
  const rank = useMutation({
    mutationFn: () => send<Ranking>(`/api/rcsa/responses/${response.id}/rank-evidence`, "POST", {}),
  });
  const channels = status.data;
  if (!channels || (!channels.prefill.enabled && !channels.evidence_relevance.enabled)) return null;
  return (
    <Card title={t("rcsa.ai.title")}>
      <p className="kn-secondary kn-text-xs">{t("rcsa.ai.disclaimer")}</p>
      {channels.prefill.enabled && (
        <section className="kn-stack-2" aria-label={t("rcsa.ai.prefill")}>
          <h5 className="kn-m-0">{t("rcsa.ai.prefill")}</h5>
          <Outbound channel={channels.prefill} />
          {editable && (
            <div className="rcsa-actions">
              <button type="button" className="kn-btn-sm" disabled={prefill.isPending} onClick={() => prefill.mutate()}>
                {t("rcsa.ai.request")}
              </button>
            </div>
          )}
          <ErrorNotice error={prefill.error} />
          {suggestions.data && suggestions.data.length > 0 && (
            <ul className="kn-stack-2">
              {suggestions.data.map((s) => (
                <SuggestionCard key={`${s.id}-${s.status}`} suggestion={s} response={response} editable={editable} onApplied={refresh} />
              ))}
            </ul>
          )}
        </section>
      )}
      {channels.evidence_relevance.enabled && canRank && (
        <section className="kn-stack-2" aria-label={t("rcsa.ai.ranking")}>
          <h5 className="kn-m-0">{t("rcsa.ai.ranking")}</h5>
          <Outbound channel={channels.evidence_relevance} />
          <div className="rcsa-actions">
            <button type="button" className="kn-btn-sm" disabled={rank.isPending} onClick={() => rank.mutate()}>
              {t("rcsa.ai.rank")}
            </button>
          </div>
          <ErrorNotice error={rank.error} />
          {rank.data && <RankingResult ranking={rank.data} />}
        </section>
      )}
    </Card>
  );
}
