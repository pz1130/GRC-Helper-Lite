import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * 跟随指针的提示框：挂到 body、position: fixed，并按视口夹住位置，
 * 所以不会被卡片的 overflow: hidden 裁掉，靠右边缘时自动翻到指针左侧。
 */
export function FloatingTip({ x, y, children }: { x: number; y: number; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ left: x + 14, top: y + 14 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const left = x + 14 + w > window.innerWidth - 8 ? x - 14 - w : x + 14;
    const top = y + 14 + h > window.innerHeight - 8 ? y - 14 - h : y + 14;
    setPos({ left: Math.max(8, left), top: Math.max(8, top) });
  }, [x, y]);
  return createPortal(
    <div ref={ref} className="kn-node-tooltip three-tip" role="tooltip" style={pos}>
      {children}
    </div>,
    document.body,
  );
}
