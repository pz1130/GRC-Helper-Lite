import { forceLayout } from "./force";
import type { GraphEdgeData, GraphNodeData, LayoutOptions, Point } from "./types";

export type LayoutKind = "document" | "function" | "force" | "bipartite";
export type { LayoutOptions, Point };

export const ROW_HEIGHT = 44;
export const TOP_PADDING = 48;
export const MIN_CANVAS_HEIGHT = 560;

/** 一个节点在某种分组口径下属于哪条泳道。没有分组信息的一律归入"未覆盖"。 */
function laneOf(node: GraphNodeData, kind: LayoutKind): string {
  if (kind === "function") return node.functions[0] ?? "—";
  return node.group ?? "—";
}

/** 分组布局：一条泳道一列，列内按 code 升序。确定性来自排序，不依赖插入顺序。 */
function laneLayout(
  nodes: GraphNodeData[],
  kind: LayoutKind,
  options: LayoutOptions,
): Map<string, Point> {
  const lanes = [...new Set(nodes.map((node) => laneOf(node, kind)))].sort();
  const positions = new Map<string, Point>();
  const laneCount = Math.max(lanes.length, 1);
  lanes.forEach((lane, laneIndex) => {
    const members = nodes
      .filter((node) => laneOf(node, kind) === lane)
      .sort((a, b) => a.code.localeCompare(b.code));
    const x = Math.round(((laneIndex + 0.5) * options.width) / laneCount);
    members.forEach((node, rowIndex) => {
      positions.set(node.key, { x, y: TOP_PADDING + rowIndex * ROW_HEIGHT });
    });
  });
  return positions;
}

/** 二部布局：左控制点按 code 升序；右框架项保持传入顺序（后端已按 order_index, code 排）。 */
function bipartiteLayout(nodes: GraphNodeData[], options: LayoutOptions): Map<string, Point> {
  const positions = new Map<string, Point>();
  const columns: [GraphNodeData["kind"], number][] = [
    ["control", Math.round(options.width * 0.22)],
    ["framework_item", Math.round(options.width * 0.78)],
  ];
  for (const [kind, x] of columns) {
    const column = nodes.filter((node) => node.kind === kind);
    const ordered = kind === "control" ? [...column].sort((a, b) => a.code.localeCompare(b.code)) : column;
    ordered.forEach((node, index) => {
      positions.set(node.key, { x, y: TOP_PADDING + index * ROW_HEIGHT });
    });
  }
  return positions;
}

function maxLaneCount(kind: LayoutKind, nodes: GraphNodeData[]): number {
  if (kind === "bipartite") {
    let controls = 0;
    let items = 0;
    for (const node of nodes) {
      if (node.kind === "control") controls += 1;
      else items += 1;
    }
    return Math.max(controls, items);
  }
  const counts = new Map<string, number>();
  for (const node of nodes) {
    const lane = laneOf(node, kind);
    counts.set(lane, (counts.get(lane) ?? 0) + 1);
  }
  return Math.max(0, ...counts.values());
}

/** 泳道/二部按内容长高，力导向保持 560，三节点夹具的 translate 断言才不会漂。 */
export function canvasHeight(kind: LayoutKind, nodes: GraphNodeData[]): number {
  if (kind === "force") return MIN_CANVAS_HEIGHT;
  return Math.max(MIN_CANVAS_HEIGHT, TOP_PADDING + maxLaneCount(kind, nodes) * ROW_HEIGHT);
}

export function layout(
  kind: LayoutKind,
  nodes: GraphNodeData[],
  edges: GraphEdgeData[],
  options: LayoutOptions,
): Map<string, Point> {
  if (kind === "force") return forceLayout(nodes, edges, options);
  if (kind === "bipartite") return bipartiteLayout(nodes, options);
  return laneLayout(nodes, kind, options);
}
