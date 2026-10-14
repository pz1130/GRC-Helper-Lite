// 3D 视图的公共判断。这个文件**不 import three**：页面在决定要不要加载 3D 分包之前
// 就要调用它，import three 会把整个库拉进主包，代码分割就白做了。

let webglCache: boolean | null = null;

/** 能否创建 WebGL 上下文。不能时页面退回 2D，不渲染一块黑画布。 */
export function hasWebGL(): boolean {
  if (webglCache !== null) return webglCache;
  try {
    const canvas = document.createElement("canvas");
    webglCache = !!(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    webglCache = false;
  }
  return webglCache;
}

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/** 读当前主题下某个 CSS 变量的颜色值，3D 材质随明暗主题取色。 */
export function cssColor(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/**
 * 2D / 3D 的初始选择，优先级从高到低：
 * 1. URL 参数 `?view=2d|3d`（排查与演示用）；
 * 2. 用户在这一页点过的选择（localStorage）；
 * 3. 自动化浏览器（navigator.webdriver，Playwright 会置位）默认 2D——
 *    e2e 断言的是 2D 画布的 DOM，不该因为默认值改了就全红；
 * 4. 有 WebGL 且没有开启「减少动态效果」时默认 3D，否则 2D。
 * 无论哪一步选了 3D，没有 WebGL 都退回 2D。
 */
export function readViewMode(key: string): "2d" | "3d" {
  const param = new URLSearchParams(window.location.search).get("view");
  const stored = localStorage.getItem(key);
  let mode: "2d" | "3d";
  if (param === "2d" || param === "3d") mode = param;
  else if (stored === "2d" || stored === "3d") mode = stored;
  else if (navigator.webdriver) mode = "2d";
  else mode = prefersReducedMotion() ? "2d" : "3d";
  return mode === "3d" && hasWebGL() ? "3d" : "2d";
}

export function writeViewMode(key: string, mode: "2d" | "3d"): void {
  localStorage.setItem(key, mode);
}
