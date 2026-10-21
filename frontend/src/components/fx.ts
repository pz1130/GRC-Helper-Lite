// 卡片跟随鼠标的柔光：把指针位置写到 --mx/--my，样式在 styles/components.css。
// 事件委托挂一次，不改任何组件。
export function initCardSpotlight(): void {
  document.addEventListener(
    "pointermove",
    (e) => {
      const card = (e.target as Element | null)?.closest?.(".ws-main .kn-card") as HTMLElement | null;
      if (!card) return;
      const r = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${e.clientX - r.left}px`);
      card.style.setProperty("--my", `${e.clientY - r.top}px`);
    },
    { passive: true },
  );
}
