// /motion-lab：动效样本陈列页（只在 ui-motion-samples 分支）。每个样本标 ID 与借鉴来源，方便 Jesse 挑选。
import { useState } from "react";
import type { CSSProperties, ReactNode } from "react";

import { PageHeader } from "../components/ui";
import { AnimeLogoDraw, AnimeLogoMorph, FlipStat, HoldButton, MagneticButton, MorphButton, NumberTicker, OverlayDemo, RadialGauge, RotatingWord, ScrambleText, SlidingNumber, SpotlightCard, TextReveal, TiltCard, ToastDemo, TraceCard } from "../components/fx";

function Sample({ id, name, source, children, isNew = false }: { id: string; name: string; source: string; children: ReactNode; isNew?: boolean }) {
  return (
    <figure className={`ml-sample${isNew ? " is-new" : ""}`} data-fx={id}>
      <div className="ml-stage">{children}</div>
      <figcaption><b>{id}</b> {name}<span>{source}</span></figcaption>
    </figure>
  );
}
function Group({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section className="ml-group" id={`group-${id}`}>
      <h3>{title}</h3>
      <div className="ml-grid">{children}</div>
    </section>
  );
}
const iv = (i: number) => ({ "--i": i }) as CSSProperties;

export function MotionLab() {
  const [on, setOn] = useState(true);
  const [replay, setReplay] = useState(0);
  return (
    <section className="kn-page ml-page">
      <PageHeader title="Motion lab" subtitle="Sample effects to pick from. IDs reference the source library each effect is ported from (plain CSS / small TS, calm palette)." />

      <Group id="a" title="A · Buttons">
        <Sample id="A1" name="Shine sweep" source="Magic UI · Shiny Button / Uiverse"><button type="button" className="fx-btn fx-shine">Generate report</button></Sample>
        <Sample id="A2" name="Glow pulse" source="Uiverse · glow button"><button type="button" className="fx-btn fx-glow fx-shine">Approve mapping</button></Sample>
        <Sample id="A3" name="Rotating halo" source="Magic UI · Shimmer Button"><button type="button" className="fx-btn fx-halo"><span>Run assessment</span></button></Sample>
        <Sample id="A4" name="Magnetic + ripple" source="Motion Primitives · Magnetic / MicroKit ripple"><MagneticButton>Start review</MagneticButton></Sample>
      </Group>

      <Group id="b" title="B · Inputs & toggles">
        <Sample id="B1" name="Floating label + underline" source="Uiverse · input / MicroKit UI"><label className="fx-field"><input placeholder=" " /><span>Control owner</span><i /></label></Sample>
        <Sample id="B2" name="Spring toggle" source="MicroKit UI · switch"><button type="button" role="switch" aria-checked={on} aria-label="Auto-escalate" className="fx-toggle" onClick={() => setOn(!on)} /></Sample>
        <Sample id="B3" name="Checkbox draw" source="Uiverse · checkbox"><label className="fx-check"><input type="checkbox" defaultChecked /><svg viewBox="0 0 22 22"><path d="M5 11.5l4 4 8-9" /></svg>Evidence verified</label></Sample>
      </Group>

      <Group id="c" title="C · Card hover">
        <Sample id="C1" name="Spotlight" source="Aceternity · Card Spotlight / Magic UI · Magic Card"><SpotlightCard><b>ISO 27001</b><p className="kn-muted">93 controls · 81% covered</p></SpotlightCard></Sample>
        <Sample id="C2" name="Border beam" source="Magic UI · Border Beam"><SpotlightCard beam><b>NIST CSF 2.0</b><p className="kn-muted">106 subcategories</p></SpotlightCard></Sample>
        <Sample id="C3" name="3D tilt + depth" source="Aceternity · 3D Card Effect"><TiltCard><b className="fx-depth" style={{ display: "block" }}>SOC 2</b><p className="kn-muted fx-depth">Type II · audit in 42 days</p></TiltCard></Sample>
      </Group>

      <Group id="d" title="D · Heading text">
        <Sample id="D1" name="Blur-in by word" source="Motion Primitives · Text Effect (blur)"><TextReveal key={`d1${replay}`} as="h3" text="Compliance posture at a glance" /></Sample>
        <Sample id="D2" name="Shiny text sweep" source="Magic UI · Animated Shiny Text / CSS Text Effects"><h3 className="fx-shiny-text kn-m-0">Governance Cockpit</h3></Sample>
        <Sample id="D3" name="Letter rise" source="CSS Text Effects · staggered letters"><TextReveal key={`d3${replay}`} as="h3" mode="letters" text="Risk Register" /></Sample>
        <button type="button" className="kn-btn-secondary kn-btn-sm ml-replay" onClick={() => setReplay(replay + 1)}>Replay text</button>
      </Group>

      <Group id="e" title="E · Number ticker">
        <Sample id="E1" name="Spring count-up" source="Magic UI · Number Ticker"><div className="ml-num"><NumberTicker key={`e1${replay}`} value={142.8} decimals={2} prefix="$" /></div></Sample>
        <Sample id="E2" name="Sliding digits" source="Motion Primitives · Sliding Number"><div className="ml-num"><SlidingNumber key={`e2${replay}`} value="2,760" /></div></Sample>
      </Group>

      <Group id="f" title="F · Loaders">
        <Sample id="F1" name="Dual arc" source="Circle Loaders"><span className="fx-arc" /></Sample>
        <Sample id="F2" name="Orbit dots" source="Circle Loaders"><span className="fx-orbit">{Array.from({ length: 8 }, (_, i) => <i key={i} style={iv(i)} />)}</span></Sample>
        <Sample id="F3" name="Pulse ring" source="Uiverse · loader"><span className="fx-pulse" /></Sample>
        <Sample id="F4" name="Bar equalizer" source="Uiverse · loader"><span className="fx-bars">{Array.from({ length: 5 }, (_, i) => <i key={i} style={iv(i)} />)}</span></Sample>
      </Group>

      <Group id="g" title="G · Backgrounds">
        <Sample id="G1" name="Dot pattern" source="Magic UI · Dot Pattern"><div className="fx-bg fx-dots ml-bg" /></Sample>
        <Sample id="G2" name="Grid + beams" source="Aceternity · Background Beams / Magic UI · Animated Grid">
          <div className="fx-bg fx-grid ml-bg">{[1, 3, 4].map((r, i) => <i key={`x${i}`} style={{ "--i": i, "--r": r } as CSSProperties} />)}{[2, 5, 8].map((r, i) => <b key={`y${i}`} style={{ "--i": i, "--r": r } as CSSProperties} />)}</div>
        </Sample>
        <Sample id="G3" name="Calm aurora wash" source="Aceternity · Aurora Background"><div className="fx-bg fx-aurora ml-bg" /></Sample>
      </Group>

      <Group id="h" title="H · SVG logo">
        <Sample id="H1" name="Logo path draw" source="Anime.js · svg.createDrawable + stagger"><AnimeLogoDraw /></Sample>
      </Group>
      <h2 className="ml-batch">Batch 2 <span className="ml-new">NEW</span></h2>

      <Group id="a2" title="A · Buttons (new)">
        <Sample isNew id="A5" name="Fill sweep + arrow" source="Uiverse · fill button"><button type="button" className="fx-btn fx-fill">View controls<span className="arr">→</span></button></Sample>
        <Sample isNew id="A6" name="Loading → success morph" source="Motion Primitives · morph button"><MorphButton /></Sample>
        <Sample isNew id="A7" name="Press & hold to confirm" source="MicroKit UI · hold button"><HoldButton done="Archived ✓">Hold to archive</HoldButton></Sample>
      </Group>

      <Group id="b2" title="B · Inputs & toggles (new)">
        <Sample isNew id="B4" name="Segmented sliding pill" source="Motion Primitives · Animated Background"><Seg /></Sample>
        <Sample isNew id="B5" name="Expanding search + focus glow" source="Uiverse · search input"><label className="fx-search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="M20 20l-4-4" /></svg><input placeholder="Search controls" aria-label="Search controls" /></label></Sample>
      </Group>

      <Group id="c2" title="C · Card hover (new)">
        <Sample isNew id="C4" name="Lift + shadow bloom" source="Uiverse · card lift"><div className="fx-card fx-lift"><span className="fx-lift-icon">◆</span><b style={{ display: "block" }}>GDPR</b><p className="kn-muted">48 controls · 12 open</p></div></Sample>
        <Sample isNew id="C5" name="Glass glare sweep" source="Aceternity · Glare Card"><div className="fx-card fx-glare"><b>DORA</b><p className="kn-muted">ICT risk · 7 findings</p></div></Sample>
        <Sample isNew id="C6" name="Pointer border glow" source="Magic UI · Magic Card (glow)"><TraceCard><b>PCI DSS 4.0</b><p className="kn-muted">12 requirements</p></TraceCard></Sample>
      </Group>

      <Group id="d2" title="D · Heading text (new)">
        <Sample isNew id="D4" name="Scramble decode" source="Magic UI · Hyper Text"><ScrambleText key={`d4${replay}`} text="AUDIT READY" /></Sample>
        <Sample isNew id="D5" name="Rotating keyword" source="Magic UI · Word Rotate"><h3 className="kn-m-0">Track <RotatingWord words={["risks", "controls", "evidence", "audits"]} /></h3></Sample>
        <Sample isNew id="D6" name="Underline draw" source="CSS Text Effects · highlight"><h3 key={`d6${replay}`} className="kn-m-0"><span className="fx-underline">Residual risk</span> trend</h3></Sample>
        <button type="button" className="kn-btn-secondary kn-btn-sm ml-replay" onClick={() => setReplay(replay + 1)}>Replay text</button>
      </Group>

      <Group id="e2" title="E · Numbers (new)">
        <Sample isNew id="E3" name="Flip stat + delta chip" source="Motion Primitives · Animated Number"><div className="ml-num"><FlipStat values={[81, 84, 79, 88]} /></div></Sample>
        <Sample isNew id="E4" name="Radial gauge count-up" source="Uiverse · progress ring"><RadialGauge key={`e4${replay}`} value={76} /></Sample>
      </Group>

      <Group id="f2" title="F · Loaders (new)">
        <Sample isNew id="F5" name="Skeleton shimmer" source="Uiverse · skeleton"><span className="fx-skel"><i /><i /><i /><i /></span></Sample>
        <Sample isNew id="F6" name="Indeterminate bar" source="Material-style progress"><span className="fx-indet" /></Sample>
        <Sample isNew id="F7" name="Typing dots" source="Uiverse · dots loader"><span className="fx-typing">{[0, 1, 2].map((i) => <i key={i} style={iv(i)} />)}</span></Sample>
      </Group>

      <Group id="g2" title="G · Backgrounds (new)">
        <Sample isNew id="G4" name="Topographic contours" source="Hero Patterns · topography (animated)"><div className="fx-bg fx-topo ml-bg" /></Sample>
        <Sample isNew id="G5" name="Slow mesh gradient" source="Magic UI · mesh / Stripe-style"><div className="fx-bg fx-mesh ml-bg" /></Sample>
        <Sample isNew id="G6" name="Rising particles" source="Magic UI · Particles (CSS)"><div className="fx-bg fx-parts ml-bg">{PARTS.map((p, i) => <i key={i} style={{ "--x": p[0], "--s": `${p[1]}px`, "--d": p[2] } as CSSProperties} />)}</div></Sample>
      </Group>

      <Group id="h2" title="H · SVG logo (new)">
        <Sample isNew id="H2" name="Shield → seal morph" source="Anime.js · svg.morphTo"><AnimeLogoMorph /></Sample>
        <Sample isNew id="H3" name="Orbiting nodes" source="CSS orbit"><div className="fx-logo-orbit">
          <i style={{ "--i": 0, "--c": "var(--fx-a)" } as CSSProperties} /><i style={{ "--i": 1, "--c": "var(--fx-b)", "--dir": "reverse" } as CSSProperties} /><i style={{ "--i": 2, "--c": "var(--fx-c)" } as CSSProperties} />
          <svg viewBox="0 0 64 64"><path d="M32 6 L52 13 V30 C52 43 43 52 32 57 C21 52 12 43 12 30 V13 Z" fill="var(--fx-a)" /><path d="M24 32 L30 38 L41 26" stroke="#fff" strokeWidth="3.4" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </div></Sample>
      </Group>

      <Group id="i" title="I · Page / route transitions (new)">
        {([["I1", "Fade + rise + blur", "fade", "Motion · AnimatePresence"], ["I2", "Horizontal slide", "slide", "View Transitions API (slide)"], ["I3", "Zoom through", "zoom", "Framer-style zoom"], ["I4", "Circular wipe", "wipe", "View Transitions API (clip-path)"]] as const).map(([id, n, k, src]) => (
          <Sample isNew key={id} id={id} name={n} source={src}><div className={`fx-pt fx-pt-${k}`}><div><b>Overview</b><i /><i /><i /></div><div><b>Risk register</b><i /><i /><i /></div></div></Sample>
        ))}
      </Group>

      <Group id="j" title="J · List & table rows (new)">
        <Sample isNew id="J1" name="Stagger rise" source="Motion · stagger"><Rows k={`j1${replay}`} cls="fx-j1" /></Sample>
        <Sample isNew id="J2" name="Slide-in with blur" source="Motion Primitives · Animated Group"><Rows k={`j2${replay}`} cls="fx-j2" /></Sample>
        <Sample isNew id="J3" name="Fold down (3D)" source="Anime.js · stagger rotateX"><Rows k={`j3${replay}`} cls="fx-j3" /></Sample>
        <Sample isNew id="J4" name="New-row highlight" source="Linear-style flash"><HlRows /></Sample>
        <button type="button" className="kn-btn-secondary kn-btn-sm ml-replay" onClick={() => setReplay(replay + 1)}>Replay rows</button>
      </Group>

      <Group id="k" title="K · Toast / modal / drawer (new)">
        <Sample isNew id="K1" name="Toast stack (spring)" source="Sonner"><ToastDemo /></Sample>
        <Sample isNew id="K2" name="Modal scale + blur backdrop" source="Radix / Motion"><OverlayDemo kind="modal" /></Sample>
        <Sample isNew id="K3" name="Side drawer" source="Vaul"><OverlayDemo kind="drawer" /></Sample>
        <Sample isNew id="K4" name="Popover menu spring" source="Radix · Popover"><OverlayDemo kind="popover" /></Sample>
      </Group>

      <Group id="l" title="L · Charts & progress (new)">
        <Sample isNew id="L1" name="Bar grow (spring)" source="Recharts-style"><div key={`l1${replay}`} className="fx-bars2">{[55, 80, 40, 95, 65, 72].map((h, i) => <i key={i} style={{ "--h": `${h}%`, "--i": i } as CSSProperties} />)}</div></Sample>
        <Sample isNew id="L2" name="Line draw + area fade" source="SVG stroke-dashoffset"><svg key={`l2${replay}`} className="fx-line" viewBox="0 0 240 120" preserveAspectRatio="none"><path className="a" d="M0 95 L40 80 L80 86 L120 55 L160 62 L200 30 L240 38 L240 120 L0 120 Z" /><path className="l" d="M0 95 L40 80 L80 86 L120 55 L160 62 L200 30 L240 38" /><circle cx="240" cy="38" r="4" /></svg></Sample>
        <Sample isNew id="L3" name="Progress bars stagger" source="Uiverse · progress"><div key={`l3${replay}`} className="fx-prog">{([["ISO 27001", 81], ["SOC 2", 64], ["NIST CSF", 47]] as const).map(([n, w], i) => <div key={n}><span><b>{n}</b>{w}%</span><u style={{ "--w": `${w}%`, "--i": i } as CSSProperties} /></div>)}</div></Sample>
        <Sample isNew id="L4" name="Donut segments sweep" source="SVG dasharray"><svg key={`l4${replay}`} className="fx-donut" viewBox="0 0 120 120">{([[105, 0, "var(--fx-a)"], [75, -109, "var(--fx-b)"], [70, -188, "var(--fx-c)"]] as const).map(([len, off, c], i) => <circle key={i} cx="60" cy="60" r="42" stroke={c} strokeDashoffset={off} style={{ "--len": len, "--i": i } as CSSProperties} />)}</svg></Sample>
        <button type="button" className="kn-btn-secondary kn-btn-sm ml-replay" onClick={() => setReplay(replay + 1)}>Replay charts</button>
      </Group>
    </section>
  );
}

const PARTS = Array.from({ length: 22 }, (_, i) => [(i * 37) % 100, 3 + (i % 4) * 2, 5 + (i % 5) * 1.5] as const);
const ROWS = [["R-014 Vendor access review", "High"], ["CC-6.1 Logical access", "Tested"], ["A.8.12 Data leakage", "Open"], ["PR.AC-1 Identity mgmt", "Mapped"]];
function Rows({ k, cls }: { k: string; cls: string }) {
  return <div key={k} className={`fx-list ${cls}`}>{ROWS.map(([a, b], i) => <div key={a} style={iv(i)}><span>{a}</span><span>{b}</span></div>)}</div>;
}
function HlRows() {
  const [rows, setRows] = useState(ROWS.slice(0, 3));
  const [hl, setHl] = useState(-1);
  const add = () => { setRows((r) => [[`R-0${20 + r.length} New finding`, "New"], ...r].slice(0, 4)); setHl((h) => h + 1); };
  return (
    <div style={{ width: "100%", display: "grid", gap: 8 }}>
      <div className="fx-list fx-j4 fx-j1">{rows.map(([a, b], i) => <div key={`${a}${i === 0 ? hl : ""}`} className={i === 0 && hl >= 0 ? "is-hl" : ""} style={iv(i)}><span>{a}</span><span>{b}</span></div>)}</div>
      <button type="button" className="kn-btn-secondary kn-btn-sm" onClick={add}>Add row</button>
    </div>
  );
}
function Seg() {
  const opts = ["Day", "Week", "Month"];
  const [v, setV] = useState(1);
  return <div className="fx-seg" style={{ "--seg": v } as CSSProperties}><i />{opts.map((o, i) => <button key={o} type="button" aria-pressed={v === i} onClick={() => setV(i)}>{o}</button>)}</div>;
}
