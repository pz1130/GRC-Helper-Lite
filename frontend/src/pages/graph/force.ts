import type { GraphEdgeData, GraphNodeData, LayoutOptions, Point } from "./types";

/** FNV-1a：把节点 key 变成 [0,1) 的定值，作为初始位置的种子。 */
function seed(key: string): number {
  let hash = 2166136261;
  for (let index = 0; index < key.length; index += 1) {
    hash = Math.imul(hash ^ key.charCodeAt(index), 16777619);
  }
  return (hash >>> 0) / 2 ** 32;
}

const ITERATIONS = 300;

/**
 * 力导向。固定种子 + 固定迭代次数 + 同步跑完再渲染：
 * 全景快照是拿去截图汇报的，两次导出不一样的图没法用。
 */
export function forceLayout(
  nodes: GraphNodeData[],
  edges: GraphEdgeData[],
  options: LayoutOptions,
): Map<string, Point> {
  const centreX = options.width / 2;
  const centreY = options.height / 2;
  const radius = Math.min(options.width, options.height) * 0.38;
  const points = nodes.map((node) => {
    const angle = seed(node.key) * Math.PI * 2;
    const distance = radius * (0.35 + 0.65 * seed(`${node.key}:r`));
    return {
      key: node.key,
      x: centreX + Math.cos(angle) * distance,
      y: centreY + Math.sin(angle) * distance,
    };
  });
  const index = new Map(points.map((point, position) => [point.key, position]));

  const repulsion = 9000;
  const springLength = 90;
  const springStrength = 0.02;

  for (let step = 0; step < ITERATIONS; step += 1) {
    const cooling = 1 - step / ITERATIONS;
    const dx = new Array(points.length).fill(0);
    const dy = new Array(points.length).fill(0);

    for (let a = 0; a < points.length; a += 1) {
      for (let b = a + 1; b < points.length; b += 1) {
        let deltaX = points[a].x - points[b].x;
        let deltaY = points[a].y - points[b].y;
        let distanceSquared = deltaX * deltaX + deltaY * deltaY;
        if (distanceSquared < 0.01) {
          // 完全重合时用 key 的先后拆开，保持确定性（不要用 Math.random）。
          deltaX = a < b ? 0.1 : -0.1;
          deltaY = 0.1;
          distanceSquared = 0.02;
        }
        const force = repulsion / distanceSquared;
        const distance = Math.sqrt(distanceSquared);
        dx[a] += (deltaX / distance) * force;
        dy[a] += (deltaY / distance) * force;
        dx[b] -= (deltaX / distance) * force;
        dy[b] -= (deltaY / distance) * force;
      }
    }

    for (const edge of edges) {
      const from = index.get(edge.source);
      const to = index.get(edge.target);
      if (from === undefined || to === undefined) continue;
      const deltaX = points[to].x - points[from].x;
      const deltaY = points[to].y - points[from].y;
      const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY) || 0.01;
      const force = (distance - springLength) * springStrength;
      dx[from] += (deltaX / distance) * force;
      dy[from] += (deltaY / distance) * force;
      dx[to] -= (deltaX / distance) * force;
      dy[to] -= (deltaY / distance) * force;
    }

    for (let position = 0; position < points.length; position += 1) {
      dx[position] += (centreX - points[position].x) * 0.008;
      dy[position] += (centreY - points[position].y) * 0.008;
      points[position].x += Math.max(-30, Math.min(30, dx[position])) * cooling;
      points[position].y += Math.max(-30, Math.min(30, dy[position])) * cooling;
    }
  }

  return fitToBox(points, options);
}

const PADDING = 28;
// 标签画在节点右侧，右边多留一点，导出的图才不会把 code 切一半。
const LABEL_ROOM = 64;

/**
 * 把跑完的点云整体平移缩放进画布。
 *
 * 少了这一步，节点数一多斥力就把点推到 viewBox 外——146 个控制点时有 81 个
 * 落在框外，屏幕上和导出的 PNG/SVG 里都被裁掉。等比缩放而不是横竖分别拉伸：
 * 力导向表达的就是距离关系，拉伸会把它扭掉。只缩不放，小图保持原样。
 */
function fitToBox(
  points: { key: string; x: number; y: number }[],
  options: LayoutOptions,
): Map<string, Point> {
  const round = (value: number) => Math.round(value * 100) / 100;
  if (points.length === 0) return new Map();

  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const point of points) {
    minX = Math.min(minX, point.x);
    maxX = Math.max(maxX, point.x);
    minY = Math.min(minY, point.y);
    maxY = Math.max(maxY, point.y);
  }

  const boxWidth = Math.max(options.width - PADDING - LABEL_ROOM, 1);
  const boxHeight = Math.max(options.height - PADDING * 2, 1);
  const spanX = maxX - minX;
  const spanY = maxY - minY;
  const scale = Math.min(
    spanX > 0 ? boxWidth / spanX : Number.POSITIVE_INFINITY,
    spanY > 0 ? boxHeight / spanY : Number.POSITIVE_INFINITY,
    1,
  );

  // 缩放后居中：把点云的中心对到可用区域的中心。
  const offsetX = PADDING + (boxWidth - spanX * scale) / 2 - minX * scale;
  const offsetY = PADDING + (boxHeight - spanY * scale) / 2 - minY * scale;

  return new Map(
    points.map((point) => [
      point.key,
      { x: round(point.x * scale + offsetX), y: round(point.y * scale + offsetY) },
    ]),
  );
}
