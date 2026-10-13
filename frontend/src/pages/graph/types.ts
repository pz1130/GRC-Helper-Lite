export interface GraphNodeData {
  key: string;
  kind: "control" | "framework_item";
  code: string;
  title: string;
  group: string | null;
  group_extra: string[];
  functions: string[];
  pending_edges: number;
  is_gap: boolean;
}

export interface GraphEdgeData {
  key: string;
  source: string;
  target: string;
  kind: string;
  status: "confirmed" | "pending";
  confidence: number | null;
  rationale: string;
  proposal_id: number | null;
  conflict_count: number;
}

export interface GraphGroupData {
  key: string;
  kind: "document" | "function";
  label: string;
}

export interface GraphData {
  nodes: GraphNodeData[];
  edges: GraphEdgeData[];
  groups: GraphGroupData[];
  stats: { nodes: number; edges: number; pending_edges: number; truncated: boolean };
}

// 坐标类型放这里而不是 layout.ts：layout.ts 要 import force.ts 的函数，
// force.ts 又要这两个类型，放在 layout.ts 会绕成环。
export interface Point {
  x: number;
  y: number;
}

export interface LayoutOptions {
  width: number;
  height: number;
}
