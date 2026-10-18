// RCSA 各页共用的小部件：模块开关、人员、错误清单、状态徽章、分页、未保存提示。
import { useQuery } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { hasText } from "../../i18n";
import { Badge, EmptyState, LoadingState, type BadgeTone } from "../../components/ui";
import { api, isConflict, listedIn, type Person } from "./api";
import "./rcsa.css";

export function useRcsaEnabled() {
  return useQuery({ queryKey: ["rcsa", "status"], queryFn: api.status, staleTime: 60_000 });
}

/** 模块关闭时不渲染任何 RCSA 页面；真正的拦截在后端（关闭时接口一律 404）。 */
export function RcsaGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const status = useRcsaEnabled();
  if (status.isPending) return <LoadingState />;
  if (!status.data?.enabled) {
    return <EmptyState title={t("rcsa.disabledTitle")} description={t("rcsa.disabledHint")} />;
  }
  return <>{children}</>;
}

export function usePeople() {
  const people = useQuery({ queryKey: ["rcsa", "people"], queryFn: api.people, staleTime: 60_000 });
  const byId = new Map((people.data ?? []).map((p) => [p.id, p]));
  return {
    people: people.data ?? [],
    nameOf: (id: number | null | undefined) => (id == null ? "—" : byId.get(id)?.name ?? `#${id}`),
  };
}

export function PersonSelect({
  label, value, onChange, people, filter, allowEmpty = true, disabled,
}: {
  label: string;
  value: number | null;
  onChange: (id: number | null) => void;
  people: Person[];
  filter?: (p: Person) => boolean;
  allowEmpty?: boolean;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <label>
      {label}
      <select
        value={value ?? ""}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
      >
        {allowEmpty && <option value="">{t("rcsa.unassigned")}</option>}
        {people.filter(filter ?? (() => true)).map((p) => (
          <option key={p.id} value={p.id} title={p.email}>{p.name}</option>
        ))}
      </select>
    </label>
  );
}

/** 一条业务错误：句子来自 en.json；有阻断项或缺项就逐条列出；版本冲突给出重新加载。 */
export function ErrorNotice({ error, onReload }: { error: unknown; onReload?: () => void }) {
  const { t } = useTranslation();
  if (!error) return null;
  const listed = [...listedIn(error, "blockers"), ...listedIn(error, "problems")];
  return (
    <div role="alert" className="rcsa-error">
      <p className="kn-danger-text kn-m-0">{error instanceof Error ? error.message : String(error)}</p>
      {listed.length > 0 && (
        <ul className="rcsa-blockers">
          {listed.map((entry, index) => (
            <li key={`${entry.code}-${index}`}>
              {hasText(`errors.${entry.code}`) ? t(`errors.${entry.code}`, { count: entry.count ?? 0 }) : entry.code}
              {entry.control_code ? <span className="kn-secondary"> — {entry.control_code}</span> : null}
            </li>
          ))}
        </ul>
      )}
      {isConflict(error) && onReload ? (
        <button type="button" className="kn-btn-sm" onClick={onReload}>{t("rcsa.reload")}</button>
      ) : null}
    </div>
  );
}

const TONES: Record<string, BadgeTone> = {
  draft: "default", pending: "default", registered: "default", open: "default",
  active: "blue", in_progress: "blue", triaged: "blue", proposed: "blue",
  submitted: "purple", in_review: "purple", pending_verification: "purple", awaiting_assessment: "purple",
  returned: "amber", no_reassessment: "amber", expiring: "amber",
  approved: "emerald", closed: "emerald", verified: "emerald", completed: "emerald",
  excluded: "cyan", cancelled: "ruby", expired: "ruby",
};

export function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  return <Badge tone={TONES[status] ?? "default"}>{t(`rcsa.status.${status}`)}</Badge>;
}

export function Pager({
  total, offset, limit, onChange,
}: { total: number; offset: number; limit: number; onChange: (offset: number) => void }) {
  const { t } = useTranslation();
  if (total <= limit) return null;
  return (
    <nav className="kn-row kn-row-gap rcsa-pager" aria-label={t("rcsa.pager")}>
      <button type="button" className="kn-btn-sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        {t("rcsa.previous")}
      </button>
      <span className="kn-secondary">{t("rcsa.pageOf", { from: offset + 1, to: Math.min(total, offset + limit), total })}</span>
      <button type="button" className="kn-btn-sm" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
        {t("rcsa.next")}
      </button>
    </nav>
  );
}

/**
 * 有未保存的修改时，刷新/关闭标签页由浏览器弹确认；站内链接在这里拦一下。
 * 路由是声明式的 BrowserRouter，没有 useBlocker，所以按点击捕获链接。
 */
export function useUnsavedGuard(dirty: boolean) {
  const { t } = useTranslation();
  useEffect(() => {
    if (!dirty) return;
    const message = t("rcsa.unsaved");
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = message;
    };
    const click = (event: MouseEvent) => {
      const anchor = (event.target as HTMLElement | null)?.closest("a[href]");
      if (anchor && !window.confirm(message)) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", click, true);
    };
  }, [dirty, t]);
}

export function Tabs<T extends string>({
  value, onChange, options, label,
}: { value: T; onChange: (value: T) => void; options: { value: T; label: string }[]; label: string }) {
  return (
    <div className="kn-segmented rcsa-tabs" role="tablist" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={value === option.value}
          className={`kn-segmented-item${value === option.value ? " kn-segmented-item-active" : ""}`}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
