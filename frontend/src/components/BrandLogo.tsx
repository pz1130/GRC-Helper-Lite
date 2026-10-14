import type { CSSProperties } from "react";

interface BrandLogoProps {
  size?: number;
  variant?: "tile" | "glyph";
  className?: string;
  style?: CSSProperties;
}

export function BrandLogo({ size = 32, variant = "tile", className, style }: BrandLogoProps) {
  const idPrefix = "grc-brand-logo";

  if (variant === "glyph") {
    return (
      <div
        className={className}
        style={{
          width: size,
          height: size,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          ...style,
        }}
      >
        <svg
          width={size}
          height={size}
          viewBox="0 0 64 64"
          fill="none"
          xmlns="http://www.w3.org/2000/svg" className="kn-block"
        >
          {/* Outer Hexagonal Shield Outline */}
          <path
            d="M 32 10 L 52 20 L 52 42 L 32 55 L 12 42 L 12 20 Z"
            stroke="var(--accent-emerald, #10b981)"
            strokeWidth="2.5"
            strokeLinejoin="round"
            fill="rgba(16, 185, 129, 0.08)"
          />

          {/* Interlocking G-Spine (Compliance Governance Flow) */}
          <path
            d="M 44 24 L 32 17 L 19 24 L 19 41 L 32 49 L 45 41 L 45 32 L 32 32"
            stroke="currentColor"
            strokeWidth="4.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Emerald Energy Diamond Core */}
          <polygon
            points="32,25.5 38.5,32 32,38.5 25.5,32"
            fill="var(--accent-emerald, #10b981)"
          />
          <circle cx="32" cy="32" r="2" fill="#ffffff" />
        </svg>
      </div>
    );
  }

  // 默认款：磨砂玻璃质感的圆角方块，中间是 G 盾。
  const borderRadius = Math.max(Math.round((size * 16) / 64), 4);

  return (
    <div
      className={className}
      style={{
        width: size,
        height: size,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        boxShadow: "0 4px 14px rgba(15, 23, 42, 0.08), 0 1px 3px rgba(15, 23, 42, 0.04), 0 0 12px rgba(16, 185, 129, 0.12)",
        borderRadius: `${borderRadius}px`,
        ...style,
      }}
    >
      <svg
        width={size}
        height={size}
        viewBox="0 0 64 64"
        fill="none"
        xmlns="http://www.w3.org/2000/svg" className="kn-block"
      >
        <defs>
          {/* Light Ceramic Studio White Background (matching current UI) */}
          <linearGradient id={`${idPrefix}-bg`} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="100%" stopColor="#f1f5f9" />
          </linearGradient>

          {/* Precision Emerald & Specular Platinum Rim */}
          <linearGradient id={`${idPrefix}-rim`} x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stopColor="#10b981" stopOpacity="0.5" />
            <stop offset="50%" stopColor="#cbd5e1" stopOpacity="0.6" />
            <stop offset="100%" stopColor="#94a3b8" stopOpacity="0.3" />
          </linearGradient>

          {/* Deep Slate G-Spine Gradient (High Contrast on Light Base) */}
          <linearGradient id={`${idPrefix}-g-spine`} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#0f172a" />
            <stop offset="60%" stopColor="#1e293b" />
            <stop offset="100%" stopColor="#334155" />
          </linearGradient>

          {/* Luminous Emerald Core Gradient */}
          <linearGradient id={`${idPrefix}-core`} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#10b981" />
            <stop offset="100%" stopColor="#059669" />
          </linearGradient>
        </defs>

        {/* Squircle Base Tile (Light Ceramic Glass) */}
        <rect width="64" height="64" rx="16" fill={`url(#${idPrefix}-bg)`} />
        <rect
          x="0.75"
          y="0.75"
          width="62.5"
          height="62.5"
          rx="15.25"
          stroke={`url(#${idPrefix}-rim)`}
          strokeWidth="1.5"
        />

        {/* Outer Hexagonal Shield Outline */}
        <path
          d="M 32 11 L 51 20 L 51 40 L 32 53 L 13 40 L 13 20 Z"
          stroke="#10b981"
          strokeOpacity="0.65"
          strokeWidth="1.6"
          strokeLinejoin="round"
          fill="rgba(16, 185, 129, 0.08)"
        />

        {/* Interlocking G-Spine (Compliance Governance Flow in Deep Slate) */}
        <path
          d="M 44 24 L 32 17 L 19 24 L 19 41 L 32 49 L 45 41 L 45 32 L 32 32"
          stroke={`url(#${idPrefix}-g-spine)`}
          strokeWidth="4.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {/* Emerald Energy Diamond Core */}
        <polygon
          points="32,25.5 38.5,32 32,38.5 25.5,32"
          fill={`url(#${idPrefix}-core)`}
        />
        <circle cx="32" cy="32" r="2" fill="#ffffff" />
      </svg>
    </div>
  );
}
