/**
 * 制度复审到期：驾驶舱的告警数与文档列表的筛选共用这一处，免得两边算得不一样。
 *
 * - 已被新版替换的文档不算：没人会去复审一份作废的旧版，它的到期日也不会再更新，
 *   算进去就是一条永远消不掉的「逾期」。
 * - 「今天」按本地日期。原来用 `toISOString()` 取的是 UTC 日期，东八区零点到八点
 *   之间「今天」还是昨天，当天到期的制度要到早上八点才算逾期。
 */

export type ReviewState = "overdue" | "soon" | null;

interface Reviewable {
  status: string;
  review_due_date: string | null;
}

const SOON_DAYS = 30;

function localIsoDate(at: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`;
}

export function reviewState(doc: Reviewable, now: Date = new Date()): ReviewState {
  if (doc.status === "superseded" || !doc.review_due_date) return null;
  const today = localIsoDate(now);
  const soonUntil = localIsoDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() + SOON_DAYS));
  if (doc.review_due_date < today) return "overdue";
  if (doc.review_due_date <= soonUntil) return "soon";
  return null;
}
