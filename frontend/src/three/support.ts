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

/** 2D / 3D 选择按页面记在 localStorage；默认 2D——e2e 与读屏依赖 2D 的 DOM 结构。 */
export function readViewMode(key: string): "2d" | "3d" {
  return localStorage.getItem(key) === "3d" && hasWebGL() ? "3d" : "2d";
}

export function writeViewMode(key: string, mode: "2d" | "3d"): void {
  localStorage.setItem(key, mode);
}
