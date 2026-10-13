import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, request } from "../api";
import { hasText, renderParams } from "../i18n";
import { useAuth } from "../auth";
import type { Control } from "./Controls";

interface Detail extends Control {
  sources: { clause_id: number; document_id: number; document_title: string; citation_label: string; heading_path: string; relation: string }[];
  relations: { from_control_id: number; to_control_id: number; from_control_code?: string | null; to_control_code?: string | null; relation_type: string; rationale: string }[];
  mappings?: { framework_name: string; framework_name_en?: string; code: string; title: string; strength: string }[];
  implementations?: { id: number; control_id: number; tech_asset_id: number | null; description: string; how_enforced: string; status: string; na_justification: string | null; owner_user_id: number | null; last_verified_at: string | null }[];
  evidence?: { id: number; evidence_type_id: number; control_id: number; tech_asset_id: number | null; title: string; owner_user_id: number | null; location_hint: string; last_collected_at: string | null; valid_until: string | null; file_path: string | null; status: string; intent_status?: string; display_status?: string; evidence_type_name?: string | null; tech_asset_name?: string | null }[];
}

// 后端给 code + 变量，句子在这里按语言组；detail 是缺词条时的中文兜底。
interface MergeReason {
  code: string;
  params?: Record<string, unknown>;
  detail: string;
}

interface MergePlan {
  loser_code: string;
  winner_code: string;
  moves: Record<string, number>;
  discards: (MergeReason & { table: string; id: number })[];
  blockers: MergeReason[];
  /** 执行时原样带回。后端重算的计划与它对不上就拒绝：多丢的那些没有人同意过。 */
  plan_digest: string;
}

export function ControlDetail() {
  const { id } = useParams();
  return <ControlContent key={id} id={id!} />;
}

function ControlContent({ id }: { id: string }) {
  const { t } = useTranslation();
  // hasText 而不是 defaultValue：缺失键的 handler 优先级更高，会显示成 code 的最后一段。
  const reasonText = (reason: MergeReason) => {
    const key = `mergeReasons.${reason.code}`;
    return hasText(key) ? t(key, renderParams(reason.params)) : reason.detail;
  };
  const { user } = useAuth();
  const canWrite = user?.role === "admin" || user?.role === "grc_lead";
  const client = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState<{ title: string; statement: string; category: string } | null>(null);
  // 打开编辑那一刻看到的值。保存时一起送去：库里已经被别人改了，后端拒绝而不是覆盖。
  const [base, setBase] = useState<{ title: string; statement: string; category: string | null } | null>(null);
  const [saved, setSaved] = useState(false);
  const [picking, setPicking] = useState(false);
  const [mergeQ, setMergeQ] = useState("");
  const [mergeSearch, setMergeSearch] = useState("");
  const [intoId, setIntoId] = useState<number | null>(null);
  const [preview, setPreview] = useState<MergePlan | null>(null);
  const detail = useQuery({ queryKey: ["control", id], queryFn: () => request<Detail>(`/api/controls/${id}`) });
  const data = detail.data;
  const winnerId = data?.status === "merged" ? data.merged_into_id : null;
  const winner = useQuery({
    queryKey: ["control", winnerId == null ? "" : String(winnerId)],
    queryFn: () => request<Control>(`/api/controls/${winnerId}`),
    enabled: winnerId != null,
  });
  const candidates = useQuery({
    queryKey: ["controls", mergeSearch],
    queryFn: () => request<Control[]>(`/api/controls?limit=100&q=${encodeURIComponent(mergeSearch)}`),
    enabled: picking,
  });
  const save = useMutation({
    mutationFn: () => request<Control>(`/api/controls/${id}`, { method: "PATCH", body: JSON.stringify({ ...form, category: form?.category.trim() || null, expected: base }) }),
    onSuccess: async () => { setForm(null); setBase(null); setSaved(true); await Promise.all([client.invalidateQueries({ queryKey: ["control", id] }), client.invalidateQueries({ queryKey: ["controls"] })]); },
    // 被拒绝时把最新的拉回来：取消再点编辑，就是在别人的改动之上重新改。
    onError: () => client.invalidateQueries({ queryKey: ["control", id] }),
  });
  const loadPreview = useMutation({
    mutationFn: (into_control_id: number) => request<MergePlan>(`/api/controls/${id}/merge-preview?into=${into_control_id}`),
    onSuccess: (plan, into_control_id) => {
      setIntoId(into_control_id);
      setPreview(plan);
      setPicking(false);
    },
  });
  const applyMerge = useMutation({
    mutationFn: ({ into_control_id, plan_digest }: { into_control_id: number; plan_digest: string }) =>
      request<MergePlan>(`/api/controls/${id}/merge`, { method: "POST", body: JSON.stringify({ into_control_id, plan_digest }) }),
    onError: (error, { into_control_id }) => {
      // 预览之后数据变了：直接按最新数据重算一遍给人看，错误提示留着说明为什么变了。
      if (error instanceof ApiError && error.code === "control.merge_plan_changed") {
        loadPreview.mutate(into_control_id);
      }
    },
    onSuccess: (_plan, { into_control_id }) => {
      setPreview(null);
      void client.invalidateQueries({ queryKey: ["controls"] });
      void client.invalidateQueries({ queryKey: ["control"] });
      navigate(`/controls/${into_control_id}`);
    },
  });
  function closePreview() {
    setPreview(null);
    setIntoId(null);
    applyMerge.reset();
  }
  return (
    <section>
      <p style={{ marginBottom: 16 }}>
        <Link to="/controls" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.875rem" }}>
          ← {t("controls.title")}
        </Link>
      </p>

      {detail.isPending && <p role="status">{t("common.loading")}</p>}
      {detail.error && (
        <p role="alert">
          <span>⚠️</span> {detail.error.message}{" "}
          <button className="kn-btn-sm" onClick={() => void detail.refetch()}>{t("common.retry")}</button>
        </p>
      )}

      {data && (
        <>
          {data.status === "merged" && data.merged_into_id != null && winner.data && (
            <p role="note" className="kn-card" style={{ marginBottom: 16 }}>
              <Link to={`/controls/${data.merged_into_id}`}>{t("controls.mergedInto", { code: winner.data.code })}</Link>
            </p>
          )}

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20, gap: 12 }}>
            <div>
              <h2>
                <code style={{ fontSize: "1.25rem", marginRight: 10 }}>{data.code}</code>
                {data.title}
              </h2>
              <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 6 }}>
                <span className="kn-badge">
                  {t("controls.category")}: {data.category ?? "—"}
                </span>
                <span className="kn-badge kn-badge-emerald">
                  {t("documents.status")}: {t(`controls.statuses.${data.status}`)}
                </span>
              </div>
            </div>
            {canWrite && data.status === "active" && (
              <button
                type="button"
                className="kn-btn-secondary kn-btn-sm"
                onClick={() => {
                  setPicking((open) => !open);
                  setMergeQ("");
                  setMergeSearch("");
                  loadPreview.reset();
                }}
              >
                {t("controls.mergeInto")}
              </button>
            )}
          </div>

          {picking && canWrite && data.status === "active" && (
            <div className="kn-card" style={{ marginBottom: 20 }}>
              <h3 style={{ fontSize: "1.0625rem" }}>{t("controls.mergePick")}</h3>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  setMergeSearch(mergeQ.trim());
                }}
                style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}
              >
                <input
                  aria-label={t("controls.search")}
                  placeholder={t("controls.search")}
                  value={mergeQ}
                  onChange={(e) => setMergeQ(e.target.value)}
                  style={{ width: 340, maxWidth: "100%" }}
                />
                <button type="submit" className="kn-btn-primary kn-btn-sm">{t("search.run")}</button>
                <button type="button" className="kn-btn-secondary kn-btn-sm" onClick={() => setPicking(false)}>{t("common.cancel")}</button>
              </form>
              {candidates.isPending && <p role="status">{t("common.loading")}</p>}
              {candidates.error && (
                <p role="alert">
                  <span>⚠️</span> {candidates.error.message}{" "}
                  <button type="button" className="kn-btn-sm" onClick={() => void candidates.refetch()}>{t("common.retry")}</button>
                </p>
              )}
              {loadPreview.error && <p role="alert"><span>⚠️</span> {loadPreview.error.message}</p>}
              {loadPreview.isPending && <p role="status">{t("common.loading")}</p>}
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {(candidates.data ?? []).filter((c) => c.id !== data.id).map((c) => (
                  <li key={c.id} style={{ marginBottom: 8 }}>
                    <button
                      type="button"
                      className="kn-btn-secondary"
                      disabled={loadPreview.isPending}
                      onClick={() => loadPreview.mutate(c.id)}
                      style={{ width: "100%", textAlign: "left" }}
                    >
                      <code>{c.code}</code> {c.title}
                    </button>
                  </li>
                ))}
              </ul>
              {candidates.isSuccess && (candidates.data ?? []).filter((c) => c.id !== data.id).length === 0 && (
                <p style={{ color: "var(--text-tertiary)" }}>{t("common.empty")}</p>
              )}
            </div>
          )}

          {/* Statement & Edit Pane */}
          <div className="kn-card" style={{ marginBottom: 24 }}>
            {form ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (form.title.trim() && form.statement.trim()) save.mutate();
                }}
                style={{ display: "grid", gap: 16, maxWidth: 760 }}
              >
                <label>
                  {t("controls.name")}
                  <input
                    required
                    disabled={save.isPending}
                    value={form.title}
                    onChange={(e) => setForm({ ...form, title: e.target.value })}
                    style={{ width: "100%" }}
                  />
                </label>
                <label>
                  {t("controls.statement")}
                  <textarea
                    required
                    disabled={save.isPending}
                    rows={6}
                    value={form.statement}
                    onChange={(e) => setForm({ ...form, statement: e.target.value })}
                    style={{ width: "100%" }}
                  />
                </label>
                <label>
                  {t("controls.category")}
                  <input
                    disabled={save.isPending}
                    value={form.category}
                    onChange={(e) => setForm({ ...form, category: e.target.value })}
                  />
                </label>

                {save.error && <p role="alert"><span>⚠️</span> {save.error.message}</p>}

                <div style={{ display: "flex", gap: 10, marginTop: 6 }}>
                  <button
                    className="kn-btn-primary"
                    disabled={save.isPending || !form.title.trim() || !form.statement.trim()}
                    type="submit"
                  >
                    {t("common.save")}
                  </button>
                  <button
                    type="button"
                    className="kn-btn-secondary"
                    disabled={save.isPending}
                    onClick={() => setForm(null)}
                  >
                    {t("common.cancel")}
                  </button>
                </div>
              </form>
            ) : (
              <div>
                <p style={{ whiteSpace: "pre-wrap", fontSize: "1rem", lineHeight: 1.65, color: "var(--text-primary)", margin: 0 }}>
                  {data.statement}
                </p>
                {canWrite && (
                  <div style={{ marginTop: 16 }}>
                    <button
                      className="kn-btn-secondary kn-btn-sm"
                      onClick={() => {
                        setForm({ title: data.title, statement: data.statement, category: data.category ?? "" });
                        setBase({ title: data.title, statement: data.statement, category: data.category });
                        save.reset();
                        setSaved(false);
                      }}
                    >
                      {t("common.edit")}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          {saved && <p role="status">{t("common.saved")}</p>}

          {/* Sources Section */}
          <div className="kn-card" style={{ marginBottom: 20 }}>
            <h3 style={{ fontSize: "1.0625rem" }}>
              {t("controls.sources")} · {t("controls.backedBy", { count: new Set(data.sources.map((s) => s.document_id)).size })}
            </h3>
            {data.sources.length === 0 && <p style={{ color: "var(--text-tertiary)" }}>{t("controls.noSources")}</p>}
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {data.sources.map((s) => (
                <li
                  key={`${s.document_id}-${s.clause_id}`}
                  style={{
                    padding: "10px 14px",
                    background: "var(--stage-card-subtle)",
                    borderRadius: "var(--radius-sm)",
                    marginBottom: 8,
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                  }}
                >
                  <Link to={`/documents/${s.document_id}#clause-${s.clause_id}`} style={{ fontWeight: 600 }}>
                    {s.document_title} · <code>{s.citation_label}</code>
                  </Link>
                  <span style={{ fontSize: "0.8125rem", color: "var(--text-secondary)" }}>
                    · <span className="kn-badge">{t(`controls.relationTypes.${s.relation}`, { defaultValue: s.relation })}</span> · {s.heading_path}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {/* Relations Section */}
          <div className="kn-card" style={{ marginBottom: 20 }}>
            <h3 style={{ fontSize: "1.0625rem" }}>{t("controls.relations")}</h3>
            {data.relations.length === 0 && <p style={{ color: "var(--text-tertiary)" }}>{t("common.empty")}</p>}
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {data.relations.map((r) => (
                <li
                  key={`${r.from_control_id}-${r.to_control_id}-${r.relation_type}`}
                  style={{
                    padding: "12px 14px",
                    background: "var(--stage-card-subtle)",
                    borderRadius: "var(--radius-sm)",
                    marginBottom: 8,
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <Link to={`/controls/${r.from_control_id}`} style={{ fontWeight: 600 }}>
                      {r.from_control_code ?? `#${r.from_control_id}`}
                    </Link>
                    <span style={{ color: "var(--accent-blue)" }}>→</span>
                    <Link to={`/controls/${r.to_control_id}`} style={{ fontWeight: 600 }}>
                      {r.to_control_code ?? `#${r.to_control_id}`}
                    </Link>
                    <span className="kn-badge kn-badge-blue">
                      {t(`controls.relationTypes.${r.relation_type}`, { defaultValue: r.relation_type })}
                    </span>
                  </div>
                  {r.rationale && (
                    <p style={{ margin: "6px 0 0 0", fontSize: "0.8125rem", color: "var(--text-secondary)", whiteSpace: "pre-wrap" }}>
                      {r.rationale}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </div>

          {/* Environment Implementation Section */}
          <div className="kn-card" style={{ marginBottom: 20 }}>
            <h3 style={{ fontSize: "1.0625rem" }}>{t("controls.implementations")}</h3>
            {(data.implementations ?? []).length === 0 && <p style={{ color: "var(--text-tertiary)" }}>{t("controls.noImplementations")}</p>}
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {(data.implementations ?? []).map((implementation) => (
                <li key={implementation.id} style={{ padding: "10px 14px", background: "var(--stage-card-subtle)", borderRadius: "var(--radius-sm)", marginBottom: 8 }}>
                  <span className="kn-badge kn-badge-blue">{t(`techAssets.implementationStatuses.${implementation.status}`, { defaultValue: implementation.status })}</span>{" "}
                  <span className="kn-badge">{implementation.how_enforced}</span>
                  {implementation.description && <p style={{ margin: "6px 0 0", fontSize: "0.8125rem", color: "var(--text-secondary)" }}>{implementation.description}</p>}
                </li>
              ))}
            </ul>
          </div>

          {/* Evidence Section */}
          <div className="kn-card" style={{ marginBottom: 20 }}>
            <h3 style={{ fontSize: "1.0625rem" }}>{t("controls.evidence")}</h3>
            {(data.evidence ?? []).length === 0 && <p style={{ color: "var(--text-tertiary)" }}>{t("controls.noEvidence")}</p>}
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {(data.evidence ?? []).map((item) => (
                <li key={item.id} style={{ padding: "10px 14px", background: "var(--stage-card-subtle)", borderRadius: "var(--radius-sm)", marginBottom: 8, display: "flex", alignItems: "center", gap: 10 }}>
                  <strong>{item.title}</strong>
                  <span className={`kn-badge ${item.display_status === "expired" || item.status === "expired" ? "kn-badge-ruby" : item.display_status === "valid" || item.status === "valid" ? "kn-badge-emerald" : "kn-badge-amber"}`}>
                    {t(`evidence.statuses.${item.display_status ?? item.status}`, { defaultValue: item.display_status ?? item.status })}
                  </span>
                  {item.valid_until && <span style={{ color: "var(--text-secondary)", fontSize: "0.8125rem" }}>{new Date(item.valid_until).toLocaleDateString()}</span>}
                </li>
              ))}
            </ul>
          </div>

          {/* Framework Mappings Section */}
          <div className="kn-card">
            <h3 style={{ fontSize: "1.0625rem" }}>{t("controls.frameworkMappings")}</h3>
            {(data.mappings ?? []).length === 0 && <p style={{ color: "var(--text-tertiary)" }}>{t("controls.noFrameworkMappings")}</p>}
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {(data.mappings ?? []).map((mapping) => (
                <li
                  key={`${mapping.framework_name}-${mapping.code}`}
                  style={{
                    padding: "10px 14px",
                    background: "var(--stage-card-subtle)",
                    borderRadius: "var(--radius-sm)",
                    marginBottom: 8,
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                  }}
                >
                  <span style={{ fontWeight: 600 }}>
                    {mapping.framework_name_en || mapping.framework_name}
                  </span>{" "}
                  · <code>{mapping.code}</code> {mapping.title} ·{" "}
                  <span className="kn-badge kn-badge-purple">
                    {t(`mapping.strength.${mapping.strength}`, { defaultValue: mapping.strength })}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {preview && (
            <div
              style={{
                position: "fixed",
                inset: 0,
                background: "rgba(0, 0, 0, 0.55)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                zIndex: 40,
                padding: 24,
              }}
            >
              <div
                role="dialog"
                aria-modal="true"
                aria-labelledby="control-merge-title"
                className="kn-card"
                style={{ width: 560, maxWidth: "100%", maxHeight: "80vh", overflow: "auto" }}
              >
                <h3 id="control-merge-title" style={{ fontSize: "1.0625rem" }}>
                  {t("controls.mergePreview", { loser: preview.loser_code, winner: preview.winner_code })}
                </h3>
                {preview.blockers.length > 0 && (
                  <div style={{ marginBottom: 12 }}>
                    <h4 style={{ margin: "0 0 8px", fontSize: "0.875rem" }}>{t("controls.mergeBlockers")}</h4>
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {preview.blockers.map((reason) => (
                        <li key={reason.code}>{reasonText(reason)}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {Object.entries(preview.moves).map(([table, count]) => (
                  <p key={table} style={{ margin: "0 0 8px" }}>
                    {t(`controls.mergeMoves.${table}`, { count })}
                  </p>
                ))}
                {preview.discards.length > 0 && (
                  <div style={{ margin: "12px 0" }}>
                    <h4 style={{ margin: "0 0 8px", fontSize: "0.875rem" }}>{t("controls.mergeDiscards")}</h4>
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {preview.discards.map((item) => (
                        <li key={`${item.table}-${item.id}`}>{reasonText(item)}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {applyMerge.error && <p role="alert"><span>⚠️</span> {applyMerge.error.message}</p>}
                <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
                  <button
                    type="button"
                    className="kn-btn-primary"
                    disabled={preview.blockers.length > 0 || applyMerge.isPending || intoId == null}
                    onClick={() => intoId != null && preview && applyMerge.mutate({ into_control_id: intoId, plan_digest: preview.plan_digest })}
                  >
                    {t("controls.merge")}
                  </button>
                  <button type="button" className="kn-btn-secondary" disabled={applyMerge.isPending} onClick={closePreview}>
                    {t("common.cancel")}
                  </button>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
