// 品牌视觉 · 方向 A：5×5 风险热力图（斜俯视 3D），波浪抬升 + 扫光，风险点从红区移向绿区。
// 纯 CSS/SVG，不依赖 WebGL；读屏忽略。
const HEAT = ["--hm-low", "--hm-mid", "--hm-high"];

function heat(r: number, c: number): string {
  const s = (5 - r) + c; // 左下低、右上高
  return `var(${HEAT[s <= 4 ? 0 : s <= 6 ? 1 : 2]})`;
}

const DOTS = [
  { x0: "84%", y0: "4%", x1: "8%", y1: "80%", t: "0s" },
  { x0: "66%", y0: "22%", x1: "26%", y1: "84%", t: "-2.3s" },
  { x0: "86%", y0: "40%", x1: "10%", y1: "62%", t: "-4.6s" },
];

export function BrandVisual({ mode }: { mode: "login" | "hero" }) {
  const cells = [];
  for (let r = 0; r < 5; r++)
    for (let c = 0; c < 5; c++)
      cells.push(<span key={`${r}-${c}`} className="hm-cell" style={{ "--d": r + c, "--c": heat(r, c) } as React.CSSProperties} />);
  return (
    <div className={`bv ${mode === "login" ? "bv-login" : "bv-hero"}`} aria-hidden="true">
      <div className="hm">
        <div className="hm-grid">{cells}</div>
        <div className="hm-scan" />
        {DOTS.map((d, i) => (
          <span key={i} className="hm-dot" style={{ "--x0": d.x0, "--y0": d.y0, "--x1": d.x1, "--y1": d.y1, "--t": d.t } as React.CSSProperties} />
        ))}
        <svg className="hm-shield" viewBox="0 0 100 100"><circle cx="50" cy="50" r="48" /></svg>
      </div>
    </div>
  );
}
