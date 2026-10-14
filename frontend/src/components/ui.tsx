// 共享的界面原语。页面里原来大量重复的内联样式（标题区、卡片、空态、加载态）
// 收敛到这里和 styles/ledger.css 的类上：改一处观感全站一致，
// 也让页面文件只剩业务结构，审阅时不必在 style 对象里找逻辑。
import type { HTMLAttributes, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { CountUp } from "./CountUp";

function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function PageHeader({
  title,
  subtitle,
  badge,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  badge?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="kn-page-header">
      <div className="kn-page-header-main">
        <h2 className="kn-page-title">
          {title}
          {badge}
        </h2>
        {subtitle ? <p className="kn-page-subtitle">{subtitle}</p> : null}
      </div>
      {actions ? <div className="kn-page-actions">{actions}</div> : null}
    </header>
  );
}

export function Card({
  title,
  actions,
  className,
  children,
  ...rest
}: { title?: ReactNode; actions?: ReactNode } & Omit<HTMLAttributes<HTMLDivElement>, "title">) {
  return (
    <div className={cx("kn-card", className)} {...rest}>
      {title || actions ? (
        <div className="kn-card-head">
          {title ? <h3 className="kn-card-title">{title}</h3> : <span />}
          {actions ? <div className="kn-row">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone,
  footer,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "danger" | "warning" | "success";
  footer?: ReactNode;
}) {
  return (
    <div className={cx("kn-card kn-stat-card", tone && `kn-tone-${tone}`)}>
      <div>
        <span className="kn-stat-label">{label}</span>
        <div className="kn-stat-number kn-primary-text">
          <CountUp value={value} />
        </div>
        {hint ? <div className="kn-stat-sub">{hint}</div> : null}
      </div>
      {footer}
    </div>
  );
}

export type BadgeTone = "default" | "blue" | "emerald" | "amber" | "ruby" | "purple" | "cyan";

export function Badge({ tone = "default", children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={cx("kn-badge", tone !== "default" && `kn-badge-${tone}`)}>{children}</span>;
}

/** 表格外壳：统一圆角、横向滚动与空数据时的占位，列与行仍由调用方渲染。 */
export function DataTable({
  head,
  children,
  empty,
  caption,
  className,
}: {
  head: ReactNode;
  children: ReactNode;
  empty?: boolean;
  caption?: string;
  /** 额外加在外壳上的类（例如 kn-mb-6、kn-table-flush）。 */
  className?: string;
}) {
  return (
    <div className={cx("kn-table-container", className)}>
      <table>
        {caption ? <caption className="kn-sr-only">{caption}</caption> : null}
        <thead>{head}</thead>
        <tbody>{children}</tbody>
      </table>
      {empty ? <EmptyState compact /> : null}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  compact,
  tone,
}: {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  tone?: "success";
}) {
  const { t } = useTranslation();
  return (
    <div className={cx("kn-empty", compact && "kn-empty--compact", tone && `kn-empty--${tone}`)}>
      <svg className="kn-empty-icon" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {tone === "success" ? (
          <path d="M20 6 9 17l-5-5" />
        ) : (
          <>
            <path d="M3 7l9-4 9 4-9 4-9-4z" />
            <path d="M3 12l9 4 9-4M3 17l9 4 9-4" />
          </>
        )}
      </svg>
      <p className="kn-empty-title">{title ?? t("common.empty")}</p>
      {description ? <p className="kn-empty-desc">{description}</p> : null}
      {action}
    </div>
  );
}

export function Skeleton({ width, height }: { width?: number | string; height?: number | string }) {
  return <span className="kn-skeleton" style={{ width, height }} aria-hidden="true" />;
}

/**
 * 加载态。保留 role="status" 与可见的「Loading…」文字：读屏要它，
 * e2e 里也有按这段文字等待的断言，骨架只是叠在前面的视觉提示。
 */
export function LoadingState({ lines = 3, inline }: { lines?: number; inline?: boolean }) {
  const { t } = useTranslation();
  if (inline) {
    return (
      <p role="status" className="kn-loading-inline">
        <span className="kn-spinner" aria-hidden="true" />
        {t("common.loading")}
      </p>
    );
  }
  return (
    <div role="status" className="kn-loading">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} width={`${92 - i * 18}%`} />
      ))}
      <span className="kn-loading-text">{t("common.loading")}</span>
    </div>
  );
}
