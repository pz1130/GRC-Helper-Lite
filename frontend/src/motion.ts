// 动效偏好。Windows「动画效果」关闭时浏览器会报告 prefers-reduced-motion: reduce，
// 之前我们据此把所有动画一刀切掉，部署后用户就看不到任何动效。现在改为三档：
// - "on"（默认）：完整动效；
// - "system"：跟随系统，系统要求减少时降为 reduced（保留淡入与标识微动，只去掉位移/缩放/3D 运镜）；
// - "off"：全部关闭。
// 结果写到 <html data-motion="full|reduced|off">，CSS 只看这个属性，不再直接用媒体查询。
import { useSyncExternalStore } from "react";

export type MotionPref = "system" | "on" | "off";
export type MotionLevel = "full" | "reduced" | "off";

const KEY = "grc.motion";
const listeners = new Set<() => void>();

function systemReduced(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

export function getMotionPref(): MotionPref {
  const v = typeof localStorage !== "undefined" ? localStorage.getItem(KEY) : null;
  return v === "system" || v === "off" || v === "on" ? v : "on";
}

export function motionLevel(): MotionLevel {
  const p = getMotionPref();
  if (p === "off") return "off";
  if (p === "system" && systemReduced()) return "reduced";
  return "full";
}

export function applyMotion(): void {
  document.documentElement.setAttribute("data-motion", motionLevel());
}

export function setMotionPref(p: MotionPref): void {
  localStorage.setItem(KEY, p);
  applyMotion();
  listeners.forEach((l) => l());
}

/** 启动时调用一次：应用当前档位，并在系统设置变化时同步（仅 system 档会受影响）。 */
export function initMotion(): void {
  applyMotion();
  window.matchMedia?.("(prefers-reduced-motion: reduce)").addEventListener?.("change", () => {
    applyMotion();
    listeners.forEach((l) => l());
  });
}

export function useMotionPref(): MotionPref {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    getMotionPref,
  );
}
