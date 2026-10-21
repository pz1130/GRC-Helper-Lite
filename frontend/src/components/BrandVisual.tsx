// 品牌视觉 · 方向 C：制度文件分页层叠展开，条款被逐条标亮，虚线流向右侧的框架标签（映射）。
// 纯 CSS/SVG，读屏忽略。
const LINES = [
  ["88%", false], ["72%", true], ["94%", false], ["60%", false], ["82%", true], ["70%", false], ["90%", false], ["52%", true],
] as const;
const CHIPS = [
  { label: "ISO 27001", top: "6%", d: "0s" },
  { label: "NIST CSF", top: "40%", d: "-1.6s" },
  { label: "SOC 2", top: "74%", d: "-3.2s" },
];

export function BrandVisual({ mode }: { mode: "login" | "hero" }) {
  return (
    <div className={`bv ${mode === "login" ? "bv-login" : "bv-hero"}`} aria-hidden="true">
      <div className="pp">
        {[2, 1, 0].map((i) => (
          <div key={i} className="pp-page" style={{ "--i": i } as React.CSSProperties}>
            <b />
            {LINES.map(([w, hi], k) => (
              <i key={k} className={hi && i === 0 ? "hi" : undefined} style={{ "--w": w, "--d": `${k * 0.4}s` } as React.CSSProperties} />
            ))}
          </div>
        ))}
        <svg className="pp-threads" viewBox="0 0 100 100" preserveAspectRatio="none">
          <path d="M20 30 C55 30 60 10 100 10" />
          <path d="M20 52 C55 52 60 44 100 44" />
          <path d="M20 76 C55 76 60 78 100 78" />
        </svg>
      </div>
      {CHIPS.map((c) => (
        <span key={c.label} className="pp-chip" style={{ top: c.top, "--d": c.d } as React.CSSProperties}>{c.label}</span>
      ))}
    </div>
  );
}
