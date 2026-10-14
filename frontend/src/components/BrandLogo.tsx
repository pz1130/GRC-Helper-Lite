// 品牌标识：盾形轮廓 + 三层「治理平面」+ 一笔勾选（合规通过）。
// 全部动效走 CSS（styles/brand.css）：入场时勾选描线，闲置时一道高光缓慢扫过、
// 层片轻微浮动，悬停时勾选重描。颜色取主题变量，明暗主题与收起的图标轨都能用。
// 「减少动态效果」打开时所有动画关闭，标识保持最终静止形态。
import type { CSSProperties } from "react";
import { useId } from "react";

interface BrandLogoProps {
  size?: number;
  /** tile：带底色的圆角方块（默认，用于图标轨）；glyph：无底色，只画标识本身。 */
  variant?: "tile" | "glyph";
  /** 是否带闲置循环动画。加载页、截图等场景可以关掉。 */
  animated?: boolean;
  className?: string;
  style?: CSSProperties;
}

export function BrandLogo({ size = 32, variant = "tile", animated = true, className, style }: BrandLogoProps) {
  // 同一页面会出现多个标识（图标轨 + 登录页），渐变 id 必须唯一，否则后一个会引用前一个的定义。
  const uid = useId().replace(/:/g, "");
  const cls = ["brand-mark", `brand-mark--${variant}`, animated ? "is-animated" : "", className ?? ""].filter(Boolean).join(" ");
  return (
    <span className={cls} style={{ width: size, height: size, ...style }} aria-hidden="true">
      <svg width={size} height={size} viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id={`${uid}-tile`} x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
            <stop offset="0" style={{ stopColor: "var(--brand-a)" }} />
            <stop offset="1" style={{ stopColor: "var(--brand-b)" }} />
          </linearGradient>
          <linearGradient id={`${uid}-sheen`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#fff" stopOpacity="0" />
            <stop offset="0.5" stopColor="#fff" stopOpacity="0.55" />
            <stop offset="1" stopColor="#fff" stopOpacity="0" />
          </linearGradient>
          <clipPath id={`${uid}-clip`}>
            <rect width="64" height="64" rx="16" />
          </clipPath>
        </defs>

        {variant === "tile" && <rect className="brand-tile" width="64" height="64" rx="16" fill={`url(#${uid}-tile)`} />}

        <g clipPath={`url(#${uid}-clip)`}>
          {/* 盾形轮廓 */}
          <path
            className="brand-shield"
            d="M32 9.5 50 16v15.5c0 11.2-7.6 19.4-18 23-10.4-3.6-18-11.8-18-23V16z"
            strokeWidth="2.6"
            strokeLinejoin="round"
          />
          {/* 三层治理平面：框架 / 制度 / 控制 */}
          <path className="brand-layer brand-layer--1" d="M22 24.5 32 20l10 4.5-10 4.5z" />
          <path className="brand-layer brand-layer--2" d="M22 30.5 32 26l10 4.5-10 4.5z" />
          <path className="brand-layer brand-layer--3" d="M22 36.5 32 32l10 4.5-10 4.5z" />
          {/* 勾选：合规通过 */}
          <path className="brand-check" d="M24.5 41.5 30 46.5 41 34.5" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" pathLength={1} />
          {/* 闲置高光：一道斜向亮带扫过 */}
          {variant === "tile" && <rect className="brand-sheen" x="-40" y="-10" width="28" height="90" fill={`url(#${uid}-sheen)`} transform="rotate(20)" />}
        </g>
      </svg>
    </span>
  );
}

/** 字标：「GRC」粗体 + 「Helper」常规字重，与标识并排用于侧栏顶部与登录页。 */
export function BrandWordmark({ size = 28, sub, mark = false }: { size?: number; sub?: string; /** true 时前面再放一个标识（登录页用）。 */ mark?: boolean }) {
  return (
    <span className="brand-lockup">
      {mark ? <BrandLogo size={size} /> : null}
      <span className="brand-word">
        <span className="brand-word-main">
          <b>GRC</b> Helper
        </span>
        {sub ? <span className="brand-word-sub">{sub}</span> : null}
      </span>
    </span>
  );
}
