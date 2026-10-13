/**
 * 导出前必须把 var(--token) 换成字面色值。
 * 序列化出去的 SVG 在浏览器外没有 CSS 变量，不换颜色会全丢。
 */
const COLOR_ATTRIBUTES = ["fill", "stroke", "stop-color"] as const;

/** 抛出去的是 i18n 键，翻译留给调用方——这里没有 t()。 */
export const EXPORT_TOO_LARGE = "graph.exportTooLarge";

export function serializeSvg(svg: SVGSVGElement): string {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  const originals = [svg, ...Array.from(svg.querySelectorAll("*"))];
  const copies = [clone, ...Array.from(clone.querySelectorAll("*"))];

  copies.forEach((node, index) => {
    const computed = window.getComputedStyle(originals[index] as Element);
    for (const attribute of COLOR_ATTRIBUTES) {
      const value = node.getAttribute(attribute);
      if (value && value.includes("var(")) {
        node.setAttribute(attribute, computed.getPropertyValue(attribute));
      }
    }
    // 内联样式里的 var() 同样要换成算出来的值。早先这里一律换成 transparent，
    // 等于把颜色丢掉——只是当时唯一的内联 var 是根节点背景、随后又被覆盖，才没露馅。
    const inline = (node as HTMLElement).getAttribute("style");
    if (inline && inline.includes("var(")) {
      const resolved = inline.replace(
        /([-a-zA-Z]+)\s*:\s*[^;]*var\([^;]*/g,
        (declaration, property: string) =>
          `${property}: ${computed.getPropertyValue(property) || "transparent"}`,
      );
      (node as HTMLElement).setAttribute("style", resolved);
    }
  });

  const background = window.getComputedStyle(svg).backgroundColor;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("style", `background:${background}`);
  return new XMLSerializer().serializeToString(clone);
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function downloadGraph(svg: SVGSVGElement, format: "svg" | "png"): Promise<void> {
  const markup = serializeSvg(svg);
  if (format === "svg") {
    triggerDownload(new Blob([markup], { type: "image/svg+xml" }), "graph.svg");
    return;
  }

  const viewBox = (svg.getAttribute("viewBox") ?? "0 0 960 560").split(" ").map(Number);
  const width = viewBox[2];
  const height = viewBox[3];
  const source = URL.createObjectURL(new Blob([markup], { type: "image/svg+xml" }));
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("graph.exportFailedSvg"));
      image.src = source;
    });
    for (let scale = bitmapScale(width, height); scale >= MIN_SCALE; scale /= 2) {
      const blob = await renderToBlob(image, width, height, scale);
      if (blob) {
        triggerDownload(blob, "graph.png");
        return;
      }
      // toBlob 给 null 只意味着这块画布浏览器开不出来，缩一半再试。
    }
    throw new Error(EXPORT_TOO_LARGE);
  } finally {
    URL.revokeObjectURL(source);
  }
}

// 画布的硬限制：单边上限各家浏览器不同，面积上限 Chrome 是 2^28。
// 超了 toBlob 直接返回 null，从前这里 `if (blob)` 一吞，用户看到的就是点了没反应。
const MAX_DIMENSION = 16384;
const MAX_AREA = 268_435_456;
const MIN_SCALE = 0.05;

/** 先按 2 倍图算，再让单边与面积都压到上限之内。 */
function bitmapScale(width: number, height: number): number {
  return Math.min(
    2, // 2 倍图，截图进幻灯片不糊
    MAX_DIMENSION / width,
    MAX_DIMENSION / height,
    Math.sqrt(MAX_AREA / (width * height)),
  );
}

async function renderToBlob(
  image: HTMLImageElement,
  width: number,
  height: number,
  scale: number,
): Promise<Blob | null> {
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.floor(width * scale));
  canvas.height = Math.max(1, Math.floor(height * scale));
  const context = canvas.getContext("2d");
  if (!context) throw new Error("graph.canvasUnavailable");
  context.scale(scale, scale);
  context.drawImage(image, 0, 0, width, height);
  try {
    return await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  } catch {
    return null; // 某些浏览器是抛异常而不是给 null
  }
}
