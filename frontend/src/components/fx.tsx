// 动效组件（从 ui-motion-samples 精选移植）：A6 StatusButton、A7 HoldButton、D4 ScrambleText、
// E3 FlipValue、E4 RadialGauge、F7 TypingDots。只看应用内 Animations 设置（motion.ts），不看系统偏好。
import { useEffect, useRef, useState } from "react";
import type { ButtonHTMLAttributes, CSSProperties, KeyboardEvent, PointerEvent, ReactNode } from "react";

import { motionLevel } from "../motion";
import { CountUp } from "./CountUp";

function cx(...p: Array<string | false | null | undefined>) {
  return p.filter(Boolean).join(" ");
}
const isAutomation = () => typeof navigator !== "undefined" && navigator.webdriver === true;

/** F7 Typing dots：替代转圈的加载指示。 */
export function TypingDots({ className }: { className?: string }) {
  return (
    <span className={cx("fx-typing", className)} aria-hidden="true">
      <i /><i /><i />
    </span>
  );
}

/**
 * A6 Loading → success：提交/保存按钮。pending 时收成加载点，pending 结束且没出错时短暂显示对勾。
 * 文字始终保留在读屏可见的 span 里，按钮的可访问名称不变（e2e 依赖它）。
 */
export function StatusButton({ pending, failed, className, children, ...rest }: { pending: boolean; failed?: boolean } & ButtonHTMLAttributes<HTMLButtonElement>) {
  const [ok, setOk] = useState(false);
  const was = useRef(pending);
  useEffect(() => {
    if (was.current && !pending && !failed) {
      setOk(true);
      const id = window.setTimeout(() => setOk(false), 1400);
      was.current = pending;
      return () => window.clearTimeout(id);
    }
    was.current = pending;
  }, [pending, failed]);
  const state = pending ? "busy" : ok ? "ok" : "idle";
  return (
    <button {...rest} className={cx(className, "fx-status", `is-${state}`)} aria-busy={pending || undefined}>
      <span className="fx-status-label">{children}</span>
      {state === "busy" ? <TypingDots className="fx-status-icon" /> : null}
      {state === "ok" ? (
        <svg className="fx-status-icon fx-status-check" viewBox="0 0 22 22" width="18" height="18" aria-hidden="true"><path d="M5 11.5l4 4 8-9" /></svg>
      ) : null}
    </button>
  );
}

/**
 * A7 Press & hold：危险操作按住 1.2 秒才触发 onConfirm（原有的确认逻辑原样放在 onConfirm 里）。
 * 键盘：按住 Enter / 空格同样 1.2 秒。自动化浏览器（navigator.webdriver）与 Animations=Off 时退回普通点击，
 * 与 3D 默认关闭的规则一致，保证 e2e 不需要模拟长按。
 */
export function HoldButton({ onConfirm, className, children, disabled, holdMs = 1200, ...rest }: { onConfirm: () => void; holdMs?: number } & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onClick">) {
  const [holding, setHolding] = useState(false);
  const [fired, setFired] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const plain = isAutomation() || motionLevel() === "off";
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const start = () => {
    if (disabled || plain || timer.current) return;
    setHolding(true);
    timer.current = window.setTimeout(() => {
      timer.current = undefined;
      setHolding(false);
      setFired(true);
      window.setTimeout(() => setFired(false), 500);
      onConfirm();
    }, holdMs);
  };
  const stop = () => {
    window.clearTimeout(timer.current);
    timer.current = undefined;
    setHolding(false);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (!e.repeat) start(); }
  };
  const onKeyUp = (e: KeyboardEvent<HTMLButtonElement>) => { if (e.key === "Enter" || e.key === " ") stop(); };
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled}
      className={cx(className, "fx-hold", holding && "is-hold", fired && "is-done")}
      style={{ "--hold": `${holdMs}ms` } as CSSProperties}
      title={plain ? rest.title : rest.title ?? "Press and hold to confirm"}
      onClick={plain ? () => onConfirm() : undefined}
      onPointerDown={plain ? undefined : (e: PointerEvent<HTMLButtonElement>) => { if (e.button === 0) start(); }}
      onPointerUp={plain ? undefined : stop}
      onPointerLeave={plain ? undefined : stop}
      onPointerCancel={plain ? undefined : stop}
      onKeyDown={plain ? undefined : onKeyDown}
      onKeyUp={plain ? undefined : onKeyUp}
    >
      <span className="fx-hold-label">{children}</span>
    </button>
  );
}

/** D4 Scramble decode：挂载时一次，约 0.5 秒，从左到右解出真实文字。 */
export function ScrambleText({ text }: { text: string }) {
  const [out, setOut] = useState(text);
  useEffect(() => {
    if (motionLevel() === "off" || isAutomation()) { setOut(text); return; }
    const glyphs = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghkmnpqrstuvwxyz";
    const steps = 14;
    let f = 0;
    const id = window.setInterval(() => {
      f++;
      const reveal = Math.floor((f / steps) * text.length);
      setOut(Array.from(text).map((c, i) => (i < reveal || c === " " ? c : glyphs[(i * 7 + f * 13) % glyphs.length])).join(""));
      if (f >= steps) { window.clearInterval(id); setOut(text); }
    }, 34);
    return () => window.clearInterval(id);
  }, [text]);
  return <span className="fx-scramble">{out}</span>;
}

/** E3 Flip stat + delta chip：数值变化时翻牌进入；能解析成数字且确实变了才出涨跌标签（不编造数据）。 */
export function FlipValue({ value }: { value: ReactNode }) {
  const prev = useRef<number | null>(null);
  // 首屏加载（"—" → 0 → 真值）不算变化：挂载 2.5 秒内只记录，不出涨跌标签。
  const mountedAt = useRef(Date.now());
  const [delta, setDelta] = useState<number | null>(null);
  const num = typeof value === "number" ? value : typeof value === "string" ? Number(value.replace(/[$,%\s]/g, "")) : NaN;
  useEffect(() => {
    if (!Number.isFinite(num)) return;
    if (prev.current !== null && prev.current !== num && Date.now() - mountedAt.current > 2500) {
      setDelta(num - prev.current);
      const id = window.setTimeout(() => setDelta(null), 2600);
      prev.current = num;
      return () => window.clearTimeout(id);
    }
    prev.current = num;
  }, [num]);
  return (
    <span className="fx-flipstat">
      <span key={String(value)} className="fx-flip-in"><CountUp value={value} /></span>
      {delta !== null && delta !== 0 ? (
        <em className={cx("fx-delta", delta > 0 ? "up" : "down")} aria-hidden="true">
          {delta > 0 ? "▲" : "▼"} {Math.abs(delta).toLocaleString("en-US", { maximumFractionDigits: 2 })}
        </em>
      ) : null}
    </span>
  );
}

/** E4 Radial gauge：完成率 / 比率指标。tone 只换颜色。 */
export function RadialGauge({ value, size = 64, tone, label }: { value: number; size?: number; tone?: "danger" | "warning" | "success"; label?: string }) {
  const C = 2 * Math.PI * 42;
  const v = Math.max(0, Math.min(100, value));
  return (
    <span
      className={cx("fx-gauge", tone && `fx-gauge--${tone}`)}
      role="img"
      aria-label={label ?? `${Math.round(v)}%`}
      style={{ width: size, height: size, "--dash": `${C}`, "--off": `${C * (1 - v / 100)}` } as CSSProperties}
    >
      <svg viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="42" className="t" /><circle cx="50" cy="50" r="42" className="v" /></svg>
      <b aria-hidden="true"><CountUp value={`${Math.round(v)}%`} /></b>
    </span>
  );
}

/** J4：DataTable 的 tbody 里首屏之后新增的行加高亮（MutationObserver，不改调用方）。 */
export function useNewRowHighlight(ref: { current: HTMLTableSectionElement | null }) {
  useEffect(() => {
    const body = ref.current;
    if (!body) return;
    let ready = false;
    const arm = window.setTimeout(() => { ready = true; }, 1200);
    const mo = new MutationObserver((list) => {
      if (!ready) return;
      for (const m of list) m.addedNodes.forEach((n) => {
        if (n instanceof HTMLTableRowElement) {
          n.classList.add("fx-row-new");
          window.setTimeout(() => n.classList.remove("fx-row-new"), 1800);
        }
      });
    });
    mo.observe(body, { childList: true });
    return () => { window.clearTimeout(arm); mo.disconnect(); };
  }, [ref]);
}
