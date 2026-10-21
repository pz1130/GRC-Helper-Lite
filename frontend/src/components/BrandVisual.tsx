// 品牌视觉 · 方向 B：散落的控制点逐条连成网络，最后汇成一面盾牌并打勾；流光沿连线跑动。
// 纯 SVG + CSS，读屏忽略。
const NODES: [number, number][] = [
  [200, 70], [120, 100], [280, 100], [95, 170], [305, 170], [150, 160], [250, 160],
  [200, 200], [130, 240], [270, 240], [200, 300], [165, 270], [235, 270], [60, 120], [340, 120], [60, 260], [340, 260],
];
const LINKS: [number, number][] = [
  [0, 1], [0, 2], [1, 3], [2, 4], [1, 5], [2, 6], [5, 7], [6, 7], [3, 8], [4, 9], [7, 11], [7, 12], [8, 11], [9, 12],
  [11, 10], [12, 10], [5, 6], [13, 1], [14, 2], [15, 8], [16, 9], [13, 3], [14, 4],
];
const SHIELD = "M200 58 L318 104 C318 196 276 270 200 318 C124 270 82 196 82 104 Z";

export function BrandVisual({ mode }: { mode: "login" | "hero" }) {
  return (
    <div className={`bv ${mode === "login" ? "bv-login" : "bv-hero"}`} aria-hidden="true">
      <svg viewBox="0 0 400 400">
        <defs>
          <linearGradient id="shg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#4F9474" stopOpacity=".28" />
            <stop offset="1" stopColor="#4F9474" stopOpacity=".04" />
          </linearGradient>
        </defs>
        <circle className="sh-ring" cx="200" cy="200" r="182" />
        <g className="sh-tilt">
          {LINKS.map(([a, b], i) => (
            <line key={i} className="sh-link" x1={NODES[a][0]} y1={NODES[a][1]} x2={NODES[b][0]} y2={NODES[b][1]} style={{ "--t": `${i * 0.05}s` } as React.CSSProperties} />
          ))}
          <path className="sh-shield" d={SHIELD} />
          <path className="sh-check" d="M160 190 L190 220 L246 158" />
          {NODES.map(([x, y], i) => (
            <circle key={i} className="sh-node" cx={x} cy={y} r={i > 12 ? 3 : 4.5} style={{ "--t": `${i * 0.06}s` } as React.CSSProperties} />
          ))}
          {[0, 1.1, 2.2].map((t) => (
            <circle key={t} className="sh-pulse" r="3" style={{ offsetPath: `path("${SHIELD}")`, "--t": `${t}s` } as React.CSSProperties} />
          ))}
        </g>
      </svg>
    </div>
  );
}
