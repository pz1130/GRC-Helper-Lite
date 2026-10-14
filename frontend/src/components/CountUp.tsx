// 统计数字的「数上去」动画。只处理能解析出数值的展示文本（"2,760"、"$142.80"、"3"），
// 保留原有的前后缀与小数位；解析不了（"—"、链接等）就原样显示。
// 自动化浏览器与「减少动态效果」下直接显示终值：e2e 断言读到的永远是最终文本。
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { prefersReducedMotion } from "../three/support";

const NUM = /^([^\d-]*)(-?[\d,]*\.?\d+)(.*)$/;

function parse(value: ReactNode) {
  if (typeof value !== "number" && typeof value !== "string") return null;
  const m = NUM.exec(String(value));
  if (!m) return null;
  const raw = m[2];
  const target = Number(raw.replace(/,/g, ""));
  if (!Number.isFinite(target)) return null;
  const decimals = raw.includes(".") ? raw.split(".")[1].length : 0;
  return { prefix: m[1], suffix: m[3], target, decimals, grouped: raw.includes(",") || target >= 1000 };
}

function format(n: number, p: NonNullable<ReturnType<typeof parse>>) {
  const s = p.grouped
    ? n.toLocaleString("en-US", { minimumFractionDigits: p.decimals, maximumFractionDigits: p.decimals })
    : n.toFixed(p.decimals);
  return `${p.prefix}${s}${p.suffix}`;
}

export function CountUp({ value, duration = 800 }: { value: ReactNode; duration?: number }) {
  const parsed = parse(value);
  const skip = !parsed || parsed.target === 0 || navigator.webdriver || prefersReducedMotion();
  const [shown, setShown] = useState<string | null>(skip ? null : format(0, parsed!));
  const raf = useRef(0);
  const key = parsed ? `${parsed.prefix}|${parsed.target}|${parsed.suffix}` : String(value);

  useEffect(() => {
    if (skip || !parsed) {
      setShown(null);
      return;
    }
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setShown(format(parsed.target * eased, parsed));
      if (t < 1) raf.current = requestAnimationFrame(tick);
      else setShown(null);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
    // 只在数值本身变化时重播
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, skip, duration]);

  // 动画中的中间值对读屏无意义：用 aria-label 给出终值
  if (shown === null) return <>{value}</>;
  return <span aria-label={String(value)} className="kn-tabular">{shown}</span>;
}
