// /motion-lab：动效样本陈列页（只在 ui-motion-samples 分支）。每个样本标 ID 与借鉴来源，方便 Jesse 挑选。
import { useState } from "react";
import type { CSSProperties, ReactNode } from "react";

import { PageHeader } from "../components/ui";
import { AnimeLogoDraw, MagneticButton, NumberTicker, SlidingNumber, SpotlightCard, TextReveal, TiltCard } from "../components/fx";

function Sample({ id, name, source, children }: { id: string; name: string; source: string; children: ReactNode }) {
  return (
    <figure className="ml-sample" data-fx={id}>
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
    </section>
  );
}
