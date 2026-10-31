// 首页（概览）hero 动效候选，?hero=p1..p4 切换；动效语言参考 svelte-bits（DotGrid / CardSwap / FloatingLines / CircularText / BlurText / DecryptedText / CountUp），代码为自写。
// p1 磁吸点阵：点阵随鼠标弹性避让，定时有「审计脉冲」波纹扫过，扫到的控制点变绿；
// p2 控制卡轮换：四张控制项卡片斜向叠放，最前一张弹性落下再回到队尾；
// p3 合规流线：一束柔和流线向右汇聚成合规带，鼠标附近的线会弯开；
// p4 环形刻度：环形文字缓转，中间合规分数环以弹簧曲线填充，整体轻微磁吸跟随鼠标。
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useTheme } from "../theme";
import "./home-motion.css";

export type HomeMotion = "p1" | "p2" | "p3" | "p4";
export const HOME_MOTIONS: readonly HomeMotion[] = ["p1", "p2", "p3", "p4"];

const PAL = {
  light: { ink: "#5c7f99", ok: "#5f9466", warn: "#c9963f", risk: "#c26b52", soft: "#b9c9d4" },
  dark: { ink: "#8fa9ba", ok: "#5fae8a", warn: "#d2a24c", risk: "#c9735a", soft: "#4a5b68" },
};
const usePal = () => PAL[useTheme().theme === "dark" ? "dark" : "light"];

// 共用：画布 + 鼠标（相对画布的坐标）
function useCanvas(draw: (g: CanvasRenderingContext2D, w: number, h: number, t: number, m: { x: number; y: number; in: boolean }) => void, deps: unknown[]) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current!; const g = cv.getContext("2d")!; const m = { x: -9999, y: -9999, in: false }; let raf = 0; const t0 = performance.now();
    const fit = () => { const r = cv.getBoundingClientRect(), d = Math.min(2, devicePixelRatio || 1); cv.width = r.width * d; cv.height = r.height * d; g.setTransform(d, 0, 0, d, 0, 0); };
    const mv = (e: PointerEvent) => { const r = cv.getBoundingClientRect(); m.x = e.clientX - r.left; m.y = e.clientY - r.top; m.in = m.x > -60 && m.y > -60 && m.x < r.width + 60 && m.y < r.height + 60; };
    fit(); window.addEventListener("resize", fit); window.addEventListener("pointermove", mv);
    const loop = () => { const r = cv.getBoundingClientRect(); g.clearRect(0, 0, r.width, r.height); draw(g, r.width, r.height, (performance.now() - t0) / 1000, m); raf = requestAnimationFrame(loop); };
    loop();
    return () => { cancelAnimationFrame(raf); window.removeEventListener("resize", fit); window.removeEventListener("pointermove", mv); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return ref;
}

/* p1 磁吸点阵 */
function DotField() {
  const c = usePal(); const st = useRef<{ x: number; y: number; vx: number; vy: number; ox: number; oy: number; lit: number; kind: number }[]>([]); const dim = useRef("");
  const ref = useCanvas((g, w, h, t, m) => {
    const gap = 22;
    if (dim.current !== `${w}x${h}`) { dim.current = `${w}x${h}`; st.current = []; for (let y = gap / 2; y < h; y += gap) for (let x = gap / 2; x < w; x += gap) { const k = Math.sin(x * 12.9898 + y * 78.233) * 43758.5453; const r = k - Math.floor(k); st.current.push({ x, y, ox: x, oy: y, vx: 0, vy: 0, lit: 0, kind: r > .965 ? 2 : r > .9 ? 1 : 0 }); } }
    const P = 3.2, k = (t % P) / P, cx = w * .62 + Math.sin(Math.floor(t / P) * 2.1) * w * .18, cy = h * .45 + Math.cos(Math.floor(t / P) * 1.7) * h * .2, R = k * Math.hypot(w, h) * .55;
    g.strokeStyle = c.ok; g.globalAlpha = (1 - k) * .35; g.lineWidth = 1.5; g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.stroke(); g.globalAlpha = 1;
    for (const d of st.current) {
      let fx = (d.ox - d.x) * .08, fy = (d.oy - d.y) * .08; // 回弹弹簧
      const dx = d.x - m.x, dy = d.y - m.y, dist = Math.hypot(dx, dy);
      if (dist < 110) { const f = (1 - dist / 110) ** 2 * 6; fx += dx / (dist || 1) * f; fy += dy / (dist || 1) * f; }
      const rd = Math.abs(Math.hypot(d.ox - cx, d.oy - cy) - R); if (rd < 14) { d.lit = 1; fx += (d.ox - cx) / (R || 1) * 1.2; fy += (d.oy - cy) / (R || 1) * 1.2; }
      d.vx = (d.vx + fx) * .82; d.vy = (d.vy + fy) * .82; d.x += d.vx; d.y += d.vy; d.lit *= .975;
      const col = d.kind === 2 ? c.warn : d.kind === 1 ? c.ok : c.ink; const s = d.kind ? 2.4 : 1.4;
      g.fillStyle = d.lit > .05 && d.kind !== 2 ? c.ok : col; g.globalAlpha = d.kind ? .85 : .28 + d.lit * .6; g.beginPath(); g.arc(d.x, d.y, s + d.lit * 1.6, 0, Math.PI * 2); g.fill();
    }
    g.globalAlpha = 1;
  }, [c]);
  return <canvas ref={ref} className="hm-canvas" />;
}

/* p2 控制卡轮换 */
const CARDS = [
  { fw: "ISO 27001", id: "A.5.15", name: "访问控制", p: 92, s: "ok" },
  { fw: "SOC 2", id: "CC6.1", name: "逻辑访问", p: 78, s: "warn" },
  { fw: "GDPR", id: "Art.32", name: "处理安全", p: 86, s: "ok" },
  { fw: "等保 2.0", id: "8.1.4", name: "安全审计", p: 64, s: "risk" },
];
function CardSwap() {
  const [n, setN] = useState(0);
  useEffect(() => { const id = setInterval(() => setN((x) => x + 1), 3000); return () => clearInterval(id); }, []);
  return (
    <div className="hm-cards">
      {CARDS.map((cd, i) => {
        const ord = (i - n % CARDS.length + CARDS.length) % CARDS.length; const leaving = ord === CARDS.length - 1;
        return (
          <div key={i} className={`hm-card${leaving ? " hm-card--drop" : ""}`} style={{ "--o": ord, zIndex: 10 - ord } as CSSProperties}>
            <div className="hm-card-head"><span className="hm-card-fw">{cd.fw}</span><span className={`hm-pill hm-pill--${cd.s}`}>{cd.s === "ok" ? "有效" : cd.s === "warn" ? "待复核" : "缺口"}</span></div>
            <div className="hm-card-id">{cd.id}</div>
            <div className="hm-card-name">{cd.name}</div>
            <div className="hm-bar"><i style={{ width: ord === 0 ? `${cd.p}%` : "12%" }} className={`hm-bar--${cd.s}`} /></div>
            <div className="hm-card-foot"><span>证据覆盖</span><b>{cd.p}%</b></div>
          </div>
        );
      })}
    </div>
  );
}

/* p3 合规流线 */
function FlowLines() {
  const c = usePal();
  const ref = useCanvas((g, w, h, t, m) => {
    const N = 26;
    for (let i = 0; i < N; i++) {
      const f = i / (N - 1), y0 = h * (.08 + f * .84), yEnd = h * .5 + (f - .5) * h * .16;
      g.beginPath();
      for (let x = 0; x <= w; x += 6) {
        const u = x / w, conv = u * u * (3 - 2 * u); let y = y0 + (yEnd - y0) * conv + Math.sin(u * 7 + t * (.6 + f * .4) + i * .5) * (1 - conv * .7) * 12;
        const dx = x - m.x, dy = y - m.y, d = Math.hypot(dx, dy); if (d < 120) y += (dy / (d || 1)) * (1 - d / 120) ** 2 * 34;
        x ? g.lineTo(x, y) : g.moveTo(x, y);
      }
      const gr = g.createLinearGradient(0, 0, w, 0); const col = i % 9 === 4 ? c.warn : i % 3 === 0 ? c.ok : c.ink;
      gr.addColorStop(0, "transparent"); gr.addColorStop(.35, col); gr.addColorStop(1, col);
      g.strokeStyle = gr; g.globalAlpha = .22 + (i % 3 === 0 ? .3 : 0); g.lineWidth = i % 3 === 0 ? 1.6 : 1; g.stroke();
      // 沿线移动的光点
      const u = ((t * .12 + i * .137) % 1), x = u * w, conv = u * u * (3 - 2 * u), y = y0 + (yEnd - y0) * conv + Math.sin(u * 7 + t * (.6 + f * .4) + i * .5) * (1 - conv * .7) * 12;
      g.globalAlpha = Math.min(1, u * 4) * .9; g.fillStyle = col; g.beginPath(); g.arc(x, y, 2.2, 0, Math.PI * 2); g.fill();
    }
    g.globalAlpha = 1;
  }, [c]);
  return <canvas ref={ref} className="hm-canvas" />;
}

/* p4 环形刻度 */
function CircleScore() {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let raf = 0, tx = 0, ty = 0, x = 0, y = 0, vx = 0, vy = 0;
    const mv = (e: PointerEvent) => { const r = box.current!.getBoundingClientRect(); const dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2); const d = Math.hypot(dx, dy); const k = d < 320 ? (1 - d / 320) : 0; tx = dx * .12 * k; ty = dy * .12 * k; };
    const loop = () => { vx = (vx + (tx - x) * .08) * .8; vy = (vy + (ty - y) * .08) * .8; x += vx; y += vy; if (box.current) box.current.style.transform = `translate(${x}px,${y}px)`; raf = requestAnimationFrame(loop); };
    window.addEventListener("pointermove", mv); loop(); return () => { cancelAnimationFrame(raf); window.removeEventListener("pointermove", mv); };
  }, []);
  const text = "GOVERNANCE · RISK · COMPLIANCE · AUDIT · POLICY · ";
  return (
    <div className="hm-circle-wrap"><div className="hm-circle" ref={box}>
      <svg viewBox="0 0 240 240" className="hm-ring-text"><defs><path id="hmc" d="M120,120 m-100,0 a100,100 0 1,1 200,0 a100,100 0 1,1 -200,0" /></defs>
        <text><textPath href="#hmc">{text}{text.slice(0, 18)}</textPath></text></svg>
      <svg viewBox="0 0 240 240" className="hm-ring-ticks">{Array.from({ length: 60 }, (_, i) => <line key={i} x1="120" y1="34" x2="120" y2={i % 5 ? 40 : 44} transform={`rotate(${i * 6} 120 120)`} />)}</svg>
      <svg viewBox="0 0 240 240" className="hm-gauge"><circle cx="120" cy="120" r="62" className="hm-gauge-bg" /><circle cx="120" cy="120" r="62" className="hm-gauge-fg" pathLength="100" /></svg>
      <div className="hm-score"><CountUp text="86" /><small>合规评分</small></div>
      <span className="hm-orbit hm-orbit--1" /><span className="hm-orbit hm-orbit--2" /><span className="hm-orbit hm-orbit--3" />
    </div></div>
  );
}

export function HomeMotionVisual({ v }: { v: HomeMotion }) {
  return <div className={`hm-stage hm-stage--${v}`} aria-hidden="true">{v === "p1" ? <DotField /> : v === "p2" ? <CardSwap /> : v === "p3" ? <FlowLines /> : <CircleScore />}</div>;
}

/* 标题入场：p1 模糊逐词、p2 逐字弹起、p3 解码、p4 遮罩上滑 */
export function MotionTitle({ text, v }: { text: string; v: HomeMotion }) {
  const [shown, setShown] = useState(v === "p3" ? scramble(text, 0) : text);
  useEffect(() => {
    if (v !== "p3") return; let f = 0; const id = setInterval(() => { f++; setShown(scramble(text, f / 22)); if (f >= 22) clearInterval(id); }, 45); return () => clearInterval(id);
  }, [text, v]);
  if (v === "p3") return <>{shown}</>;
  if (v === "p2") { let n = 0; return <span className="hm-title hm-title--p2">{text.split(" ").map((w, wi) => <span key={wi} className="hm-word">{Array.from(w).map((c) => <span key={n} style={{ "--i": n++ } as CSSProperties}>{c}</span>)}{wi < text.split(" ").length - 1 ? "\u00a0" : ""}</span>)}</span>; }
  const parts = v === "p1" ? text.split(/(\s+|·)/).filter(Boolean) : [text];
  return <span className={`hm-title hm-title--${v}`}>{parts.map((p, i) => <span key={i} style={{ "--i": i } as CSSProperties}>{p === " " ? "\u00a0" : p}</span>)}</span>;
}
const GLYPH = "▚▞▤▥◧◨#%&01ΣΔ";
function scramble(s: string, k: number) { return Array.from(s).map((ch, i, a) => (i / a.length < k || ch === " " ? ch : GLYPH[(i * 7 + Math.floor(k * 40)) % GLYPH.length])).join(""); }

/* 数字滚动：解析文本里第一个数，保留前后缀与小数位 */
export function CountUp({ text, ms = 1400 }: { text: string; ms?: number }) {
  const mm = /^([^0-9]*)([0-9][0-9,]*(?:\.[0-9]+)?)(.*)$/.exec(text);
  const [val, setVal] = useState(0);
  const target = mm ? parseFloat(mm[2].replace(/,/g, "")) : 0, dec = mm?.[2].split(".")[1]?.length ?? 0;
  useEffect(() => { if (!mm) return; let raf = 0; const t0 = performance.now(); const step = () => { const k = Math.min(1, (performance.now() - t0) / ms); const e = 1 - Math.pow(1 - k, 4); setVal(target * e); if (k < 1) raf = requestAnimationFrame(step); }; step(); return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, ms]);
  if (!mm) return <>{text}</>;
  const num = val.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec, useGrouping: mm[2].includes(",") || target >= 1000 });
  return <>{mm[1]}{num}{mm[3]}</>;
}
