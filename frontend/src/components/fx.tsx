// 动效样本的小组件：只在需要指针 / 计时的地方用 TS，其余全是 fx.css。
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as RPE, ReactNode } from "react";

import { motionLevel } from "../motion";

/** C1 Spotlight：把指针位置写进 CSS 变量。 */
export function SpotlightCard({ children, className = "", beam = false }: { children: ReactNode; className?: string; beam?: boolean }) {
  const onMove = (e: RPE<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--px", `${e.clientX - r.left}px`);
    e.currentTarget.style.setProperty("--py", `${e.clientY - r.top}px`);
  };
  return <div className={`fx-card fx-spotlight${beam ? " fx-beam" : ""} ${className}`} onPointerMove={onMove}>{children}</div>;
}

/** C3 3D tilt：最大 ±10°。 */
export function TiltCard({ children, className = "" }: { children: ReactNode; className?: string }) {
  const onMove = (e: RPE<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    e.currentTarget.style.setProperty("--ry", `${x * 20}deg`);
    e.currentTarget.style.setProperty("--rx", `${-y * 20}deg`);
  };
  const reset = (e: RPE<HTMLDivElement>) => { e.currentTarget.style.setProperty("--rx", "0deg"); e.currentTarget.style.setProperty("--ry", "0deg"); };
  return <div className="fx-tilt-wrap"><div className={`fx-card fx-tilt ${className}`} onPointerMove={onMove} onPointerLeave={reset}>{children}</div></div>;
}

/** A4 Magnetic + ripple 按钮。 */
export function MagneticButton({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLButtonElement>(null);
  const onMove = (e: RPE<HTMLButtonElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--mx", `${(e.clientX - r.left - r.width / 2) * 0.25}px`);
    e.currentTarget.style.setProperty("--my", `${(e.clientY - r.top - r.height / 2) * 0.35}px`);
  };
  const onLeave = () => { ref.current?.style.setProperty("--mx", "0px"); ref.current?.style.setProperty("--my", "0px"); };
  const onDown = (e: RPE<HTMLButtonElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const dot = document.createElement("span");
    const s = Math.max(r.width, r.height) / 2;
    dot.className = "fx-ripple-dot";
    Object.assign(dot.style, { width: `${s}px`, height: `${s}px`, left: `${e.clientX - r.left - s / 2}px`, top: `${e.clientY - r.top - s / 2}px` });
    e.currentTarget.appendChild(dot);
    setTimeout(() => dot.remove(), 650);
  };
  return <button ref={ref} type="button" className="fx-btn fx-magnetic" onPointerMove={onMove} onPointerLeave={onLeave} onPointerDown={onDown}>{children}</button>;
}

/** D1 / D3：按词或按字母拆开做错峰入场。key 变化即重播。 */
export function TextReveal({ text, mode = "words", as: Tag = "span", className = "" }: { text: string; mode?: "words" | "letters"; as?: "span" | "h1" | "h2" | "h3"; className?: string }) {
  const parts = mode === "words" ? text.split(/(\s+)/) : Array.from(text);
  let i = 0;
  return (
    <Tag className={`${mode === "words" ? "fx-words" : "fx-letters"} ${className}`} aria-label={text}>
      {parts.map((p, k) => (/^\s+$/.test(p) ? p : <span key={k} aria-hidden="true" style={{ "--i": i++ } as CSSProperties}>{p === " " ? "\u00a0" : p}</span>))}
    </Tag>
  );
}

/** E1 Number ticker（Magic UI「Number Ticker」）：弹簧式缓动计数；进入视口才开始。 */
export function NumberTicker({ value, decimals = 0, prefix = "", duration = 1600 }: { value: number; decimals?: number; prefix?: string; duration?: number }) {
  const [shown, setShown] = useState(motionLevel() === "off" ? value : 0);
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (motionLevel() === "off") { setShown(value); return; }
    let raf = 0, start = 0;
    const io = new IntersectionObserver(([en]) => {
      if (!en.isIntersecting) return;
      io.disconnect();
      const step = (now: number) => {
        if (!start) start = now;
        const t = Math.min(1, (now - start) / duration);
        // easeOutExpo + 轻微回弹
        const e = t === 1 ? 1 : 1 - Math.pow(2, -10 * t) + Math.sin(t * Math.PI) * 0.02;
        setShown(value * e);
        if (t < 1) raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
    });
    if (ref.current) io.observe(ref.current);
    return () => { io.disconnect(); cancelAnimationFrame(raf); };
  }, [value, duration]);
  return <span ref={ref} className="kn-tabular">{prefix}{shown.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}</span>;
}

/** E2 Slot-roll digits（Motion Primitives「Sliding Number」）：每位数字纵向滚动到位。 */
export function SlidingNumber({ value }: { value: string }) {
  const [go, setGo] = useState(false);
  useEffect(() => { const t = setTimeout(() => setGo(true), 120); return () => clearTimeout(t); }, [value]);
  return (
    <span className="fx-slide-num" aria-label={value}>
      {Array.from(value).map((ch, i) =>
        /\d/.test(ch) ? (
          <span key={i} className="fx-slide-col" aria-hidden="true">
            <span style={{ transform: `translateY(${go ? -Number(ch) * 10 : 0}%)`, transitionDelay: `${i * 60}ms` }}>
              {"0123456789".split("").map((d) => <span key={d}>{d}</span>)}
            </span>
          </span>
        ) : <span key={i} aria-hidden="true">{ch}</span>,
      )}
    </span>
  );
}

/** H1 Logo 描线（Anime.js svg.createDrawable）：anime.js 按需加载，只在这个组件挂载时才下载。 */
export function AnimeLogoDraw({ size = 120, loop = true }: { size?: number; loop?: boolean }) {
  const ref = useRef<SVGSVGElement>(null);
  useEffect(() => {
    let cancelled = false;
    let anims: { revert?: () => void; pause?: () => void }[] = [];
    if (motionLevel() === "off") return;
    import("animejs").then(({ animate, svg, stagger }) => {
      if (cancelled || !ref.current) return;
      const draw = svg.createDrawable(ref.current.querySelectorAll(".ld"));
      anims.push(animate(draw, { draw: ["0 0", "0 1"], ease: "inOutQuad", duration: 1400, delay: stagger(180), loop, alternate: loop, loopDelay: 900 }));
      anims.push(animate(ref.current.querySelectorAll(".lf"), { opacity: [0, 1], scale: [0.6, 1], ease: "outBack", duration: 700, delay: stagger(120, { start: 900 }), loop, alternate: loop, loopDelay: 1600 }));
    });
    return () => { cancelled = true; anims.forEach((a) => a.revert?.()); anims = []; };
  }, [loop]);
  return (
    <svg ref={ref} width={size} height={size} viewBox="0 0 64 64" className="fx-logo-draw-svg" aria-hidden="true">
      <path className="ld" d="M32 6 L52 13 V30 C52 43 43 52 32 57 C21 52 12 43 12 30 V13 Z" stroke="var(--fx-a)" strokeWidth="2.4" fill="none" strokeLinejoin="round" />
      <path className="ld" d="M21 24 L32 19 L43 24 L32 29 Z" stroke="var(--fx-b)" strokeWidth="2" fill="none" strokeLinejoin="round" />
      <path className="ld" d="M21 31 L32 36 L43 31" stroke="var(--fx-b)" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <path className="ld" d="M25 41 L30 46 L41 35" stroke="var(--fx-c)" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      <circle className="lf" cx="21" cy="24" r="2" fill="var(--fx-a)" style={{ transformOrigin: "21px 24px" }} />
      <circle className="lf" cx="43" cy="24" r="2" fill="var(--fx-a)" style={{ transformOrigin: "43px 24px" }} />
      <circle className="lf" cx="32" cy="36" r="2" fill="var(--fx-b)" style={{ transformOrigin: "32px 36px" }} />
    </svg>
  );
}

/* ================= Batch 2 ================= */

/** 重播 key：点按钮让子树重新挂载。 */
export function useReplay(): [number, () => void] {
  const [k, setK] = useState(0);
  return [k, () => setK((v) => v + 1)];
}

/** A7 Press-and-hold confirm：按住 1.2s 填满后确认（用于高风险操作）。 */
export function HoldButton({ children, done = "Confirmed" }: { children: ReactNode; done?: string }) {
  const [state, setState] = useState<"idle" | "hold" | "done">("idle");
  const t = useRef<number | undefined>(undefined);
  const start = () => { if (state === "done") { setState("idle"); return; } setState("hold"); t.current = window.setTimeout(() => setState("done"), 1200); };
  const stop = () => { clearTimeout(t.current); setState((s) => (s === "done" ? s : "idle")); };
  return (
    <button type="button" className={`fx-btn fx-hold is-${state}`} onPointerDown={start} onPointerUp={stop} onPointerLeave={stop}>
      <span>{state === "done" ? done : children}</span>
    </button>
  );
}

/** A6 Loading → success morph 按钮。 */
export function MorphButton() {
  const [s, setS] = useState<"idle" | "busy" | "ok">("idle");
  const click = () => {
    if (s !== "idle") { setS("idle"); return; }
    setS("busy"); setTimeout(() => setS("ok"), 1300);
  };
  return (
    <button type="button" className={`fx-btn fx-shine fx-morph is-${s}`} onClick={click} aria-live="polite">
      {s === "idle" && "Submit evidence"}
      {s === "busy" && <span className="fx-morph-spin" aria-label="Submitting" />}
      {s === "ok" && <svg viewBox="0 0 22 22" width="20" height="20" aria-label="Done"><path d="M5 11.5l4 4 8-9" /></svg>}
    </button>
  );
}

/** C5 Glare sweep / C4 lift 卡片沿用 CSS；C6 border trace 用 conic + 指针角度。 */
export function TraceCard({ children }: { children: ReactNode }) {
  const onMove = (e: RPE<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const a = Math.atan2(e.clientY - r.top - r.height / 2, e.clientX - r.left - r.width / 2) * 180 / Math.PI + 90;
    e.currentTarget.style.setProperty("--ang", `${a}deg`);
  };
  return <div className="fx-card fx-trace" onPointerMove={onMove}>{children}</div>;
}

/** D4 Scramble / decode 文本（类似 Hyper text）。 */
export function ScrambleText({ text, className = "" }: { text: string; className?: string }) {
  const [out, setOut] = useState(motionLevel() === "off" ? text : "");
  useEffect(() => {
    if (motionLevel() === "off") { setOut(text); return; }
    const glyphs = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789";
    let f = 0; const total = text.length * 3 + 10;
    const id = setInterval(() => {
      f++;
      setOut(Array.from(text).map((c, i) => (c === " " || f > i * 3 + 10 ? c : glyphs[Math.floor(Math.random() * glyphs.length)])).join(""));
      if (f >= total) clearInterval(id);
    }, 32);
    return () => clearInterval(id);
  }, [text]);
  return <span className={`fx-scramble ${className}`} aria-label={text}>{out}</span>;
}

/** D5 Rotating word：几个词轮流从下方滑入。 */
export function RotatingWord({ words }: { words: string[] }) {
  const [i, setI] = useState(0);
  useEffect(() => { const id = setInterval(() => setI((v) => (v + 1) % words.length), 1800); return () => clearInterval(id); }, [words.length]);
  return <span className="fx-rotword"><span key={i}>{words[i]}</span></span>;
}

/** E3 Delta chip：数值跳变时上下翻牌 + 涨跌色。 */
export function FlipStat({ values }: { values: number[] }) {
  const [i, setI] = useState(0);
  useEffect(() => { const id = setInterval(() => setI((v) => (v + 1) % values.length), 1600); return () => clearInterval(id); }, [values.length]);
  const v = values[i], prev = values[(i - 1 + values.length) % values.length], up = v >= prev;
  return (
    <span className="fx-flipstat">
      <span key={`v${i}`} className="fx-flip-in">{v}%</span>
      <em key={`d${i}`} className={`fx-delta ${up ? "up" : "down"}`}>{up ? "▲" : "▼"} {Math.abs(v - prev).toFixed(0)}</em>
    </span>
  );
}

/** E4 Radial gauge：环形进度 + 中间计数。 */
export function RadialGauge({ value }: { value: number }) {
  const C = 2 * Math.PI * 42;
  return (
    <div className="fx-gauge" style={{ "--dash": `${C}`, "--off": `${C * (1 - value / 100)}` } as CSSProperties}>
      <svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="42" className="t" /><circle cx="50" cy="50" r="42" className="v" /></svg>
      <b><NumberTicker value={value} />%</b>
    </div>
  );
}

/** H2 Logo morph：盾形 ↔ 勾，anime.js 按需加载做路径补间。H3 用 CSS 轨道。 */
export function AnimeLogoMorph({ size = 120 }: { size?: number }) {
  const ref = useRef<SVGSVGElement>(null);
  useEffect(() => {
    let cancelled = false; let a: { revert?: () => void } | undefined;
    if (motionLevel() === "off") return;
    import("animejs").then(({ animate, svg }) => {
      if (cancelled || !ref.current) return;
      const from = ref.current.querySelector(".m0") as SVGPathElement, to = ref.current.querySelector(".m1") as SVGPathElement;
      a = animate(from, { d: svg.morphTo(to), duration: 1300, ease: "inOutCubic", loop: true, alternate: true, loopDelay: 700 });
    });
    return () => { cancelled = true; a?.revert?.(); };
  }, []);
  return (
    <svg ref={ref} width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <path className="m0" d="M32 6 L52 13 V30 C52 43 43 52 32 57 C21 52 12 43 12 30 V13 Z" fill="var(--fx-a)" opacity=".9" />
      <path className="m1" d="M32 8 C46 8 56 18 56 32 C56 46 46 56 32 56 C18 56 8 46 8 32 C8 18 18 8 32 8 Z" fill="none" style={{ display: "none" }} />
      <path d="M24 32 L30 38 L41 26" stroke="#fff" strokeWidth="3.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** K1 Toast stack：点击推入，4s 自动消失。 */
export function ToastDemo() {
  const [items, setItems] = useState<number[]>([]);
  const n = useRef(0);
  const push = () => { const id = ++n.current; setItems((x) => [...x.slice(-2), id]); setTimeout(() => setItems((x) => x.filter((v) => v !== id)), 4000); };
  const msgs = ["Control CC-6.1 mapped", "Evidence uploaded", "Risk R-014 reassessed"];
  return (
    <div className="ml-k">
      <button type="button" className="fx-btn fx-shine" onClick={push}>Show toast</button>
      <div className="fx-toasts">{items.map((id) => <div key={id} className="fx-toast"><i />{msgs[id % 3]}</div>)}</div>
    </div>
  );
}

/** K2 / K3：modal（scale + backdrop blur）与 drawer（右侧滑入），局限在样本框内。 */
export function OverlayDemo({ kind }: { kind: "modal" | "drawer" | "popover" }) {
  const [open, setOpen] = useState(false);
  const label = kind === "modal" ? "Open dialog" : kind === "drawer" ? "Open drawer" : "Open menu";
  return (
    <div className={`ml-k fx-ov-host ${open ? "is-open" : ""}`}>
      <button type="button" className="fx-btn fx-shine" onClick={() => setOpen(true)}>{label}</button>
      <div className="fx-ov-backdrop" onClick={() => setOpen(false)} />
      <div className={`fx-ov fx-ov-${kind}`} role="dialog" aria-hidden={!open}>
        <b>{kind === "modal" ? "Archive policy?" : kind === "drawer" ? "Control CC-6.1" : "Actions"}</b>
        <p className="kn-muted">{kind === "popover" ? "Assign · Export · Archive" : "Owner: Dana Lee · Last tested 12 Oct"}</p>
        <button type="button" className="kn-btn-secondary kn-btn-sm" onClick={() => setOpen(false)}>Close</button>
      </div>
    </div>
  );
}
