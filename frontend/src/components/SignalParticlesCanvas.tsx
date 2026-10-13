import { useEffect, useRef } from "react";

export interface SignalParticlesCanvasProps {
  mode?: "dark" | "light";
  speed?: number;
  className?: string;
  style?: React.CSSProperties;
}

export function SignalParticlesCanvas({
  mode = "dark",
  speed = 1.0,
  className,
  style,
}: SignalParticlesCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const modeRef = useRef(mode);
  const speedRef = useRef(speed);

  modeRef.current = mode;
  speedRef.current = speed;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return;

    let animId = 0;
    let width = 0;
    let height = 0;
    let isVisible = true;
    let time = 0;

    const spacing = 16;
    const dotRadius = 1.5;

    function resize() {
      if (!canvas || !ctx) return;
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.max(1, Math.floor(width * dpr));
      canvas.height = Math.max(1, Math.floor(height * dpr));
      ctx.resetTransform?.();
      ctx.scale(dpr, dpr);
    }

    const observer = new ResizeObserver(() => {
      resize();
    });
    observer.observe(canvas);
    resize();

    function draw() {
      if (!ctx || width <= 0 || height <= 0) return;

      const isLight = modeRef.current === "light";
      const spd = speedRef.current;

      // Fill background
      ctx.fillStyle = isLight ? "#f8fafc" : "#060b10";
      ctx.fillRect(0, 0, width, height);

      const cols = Math.floor(width / spacing);
      const rows = Math.floor(height / spacing);
      const offsetX = (width - cols * spacing) / 2;
      const offsetY = (height - rows * spacing) / 2;

      for (let i = 0; i <= cols; i++) {
        for (let j = 0; j <= rows; j++) {
          const x = offsetX + i * spacing;
          const y = offsetY + j * spacing;

          const nx = i * 0.1;
          const ny = j * 0.1;

          const wave1 = Math.sin(nx + time * 0.5) * Math.cos(ny - time * 0.3);
          const wave2 = Math.sin(nx * 0.5 - ny * 0.5 + time * 0.8);
          const value = wave1 + wave2;

          if (value > 0.1) {
            ctx.beginPath();
            ctx.arc(x, y, dotRadius, 0, Math.PI * 2);

            const highlightCheck = Math.sin(i * 12.34) * Math.cos(j * 56.78);

            if (highlightCheck > 0.98) {
              // Subtle blue/cyan pulse
              ctx.fillStyle = isLight ? "#0284c7" : "#38bdf8";
            } else if (highlightCheck < -0.98) {
              // 克制的青绿色脉冲，跟主色板走。
              ctx.fillStyle = isLight ? "#059669" : "#10b981";
            } else {
              const alpha = Math.min(0.65, (value - 0.1) * 0.8);
              ctx.fillStyle = isLight
                ? `rgba(71, 85, 105, ${alpha})`
                : `rgba(148, 163, 184, ${alpha})`;
            }

            ctx.fill();
          }
        }
      }

      time += 0.02 * spd;
      if (isVisible && !document.hidden) {
        animId = requestAnimationFrame(draw);
      } else {
        animId = 0;
      }
    }

    function onVisibilityChange() {
      if (document.hidden) {
        if (animId) {
          cancelAnimationFrame(animId);
          animId = 0;
        }
      } else if (isVisible && !animId) {
        animId = requestAnimationFrame(draw);
      }
    }

    const intersection = new IntersectionObserver(([entry]) => {
      isVisible = entry?.isIntersecting ?? true;
      if (isVisible && !animId && !document.hidden) {
        animId = requestAnimationFrame(draw);
      } else if (!isVisible && animId) {
        cancelAnimationFrame(animId);
        animId = 0;
      }
    });
    intersection.observe(canvas);

    document.addEventListener("visibilitychange", onVisibilityChange);
    animId = requestAnimationFrame(draw);

    return () => {
      if (animId) cancelAnimationFrame(animId);
      observer.disconnect();
      intersection.disconnect();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className={className}
      style={{
        display: "block",
        width: "100%",
        height: "100%",
        ...style,
      }}
    />
  );
}

// 兼容旧名字的导出。
export const PredictiveArcCanvas = SignalParticlesCanvas;
