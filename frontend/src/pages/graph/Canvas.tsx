import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { LayoutKind, Point } from "./layout";
import { layout } from "./layout";
import type { GraphData, GraphEdgeData, GraphNodeData } from "./types";

const EDGE_COLOR: Record<string, string> = {
  depends_on: "var(--accent-blue)",
  duplicates: "var(--accent-purple)",
  // M10 的 conflict_detection 一落库就自动点亮，这里不必回头改。
  conflicts_with: "var(--accent-ruby)",
  full: "var(--accent-emerald)",
  partial: "var(--accent-cyan)",
  supporting: "var(--accent-amber)",
};

export interface CanvasProps {
  data: GraphData;
  layoutKind: LayoutKind;
  width: number;
  height: number;
  onSelectNode: (node: GraphNodeData) => void;
  onSelectEdge: (edge: GraphEdgeData) => void;
  selectedNodeKey?: string | null;
  selectedEdgeKey?: string | null;
  labelLimit?: number;
  thinLabels?: boolean;
}

export function Canvas({
  data,
  layoutKind,
  width,
  height,
  onSelectNode,
  onSelectEdge,
  selectedNodeKey,
  selectedEdgeKey,
  labelLimit,
  thinLabels,
}: CanvasProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  // Zoom & Pan 状态
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const dragStart = useRef({ clientX: 0, clientY: 0, panX: 0, panY: 0 });

  // 交互悬浮状态
  const [hoveredNodeKey, setHoveredNodeKey] = useState<string | null>(null);
  const [hoveredEdgeKey, setHoveredEdgeKey] = useState<string | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number } | null>(null);

  // 布局或数据变化时，重置视口
  useEffect(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, [layoutKind, data]);

  const positions: Map<string, Point> = useMemo(
    () => layout(layoutKind, data.nodes, data.edges, { width, height }),
    [layoutKind, data.nodes, data.edges, width, height],
  );

  // 节点连接关系图谱缓存（用于关联高亮与度数统计）
  const nodeConnections = useMemo(() => {
    const map = new Map<
      string,
      { total: number; connectedEdges: Set<string>; neighborNodes: Set<string> }
    >();
    for (const node of data.nodes) {
      map.set(node.key, { total: 0, connectedEdges: new Set(), neighborNodes: new Set([node.key]) });
    }
    for (const edge of data.edges) {
      const src = map.get(edge.source);
      if (src) {
        src.total++;
        src.connectedEdges.add(edge.key);
        src.neighborNodes.add(edge.target);
      }
      const tgt = map.get(edge.target);
      if (tgt) {
        tgt.total++;
        tgt.connectedEdges.add(edge.key);
        tgt.neighborNodes.add(edge.source);
      }
    }
    return map;
  }, [data.nodes, data.edges]);

  const activeNodeKey = hoveredNodeKey ?? selectedNodeKey ?? null;
  const activeEdgeKey = hoveredEdgeKey ?? selectedEdgeKey ?? null;
  const activeConn = activeNodeKey ? nodeConnections.get(activeNodeKey) : null;
  const hasActiveFilter = Boolean(activeNodeKey || activeEdgeKey);

  // 全景下按度数稀释标签
  const labelled = useMemo(() => {
    const degree = new Map<string, number>();
    for (const edge of data.edges) {
      degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
      degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
    }
    return new Set(
      labelLimit === undefined
        ? data.nodes.map((node) => node.key)
        : [...data.nodes]
            .sort((a, b) => (degree.get(b.key) ?? 0) - (degree.get(a.key) ?? 0) || a.code.localeCompare(b.code))
            .slice(0, labelLimit)
            .map((node) => node.key),
    );
  }, [data.edges, data.nodes, labelLimit]);

  // 背景拖拽平移
  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.button !== 0) return;
    const target = e.target as HTMLElement;
    if (target.closest && target.closest("[data-node-key], [data-edge-key], .kn-canvas-hud, .kn-node-tooltip")) {
      return;
    }
    setIsDragging(true);
    dragStart.current = {
      clientX: e.clientX,
      clientY: e.clientY,
      panX: pan.x,
      panY: pan.y,
    };
  };

  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      if (!svgRef.current) return;
      const rect = svgRef.current.getBoundingClientRect();
      const scaleX = width / rect.width;
      const scaleY = height / rect.height;
      const dx = (e.clientX - dragStart.current.clientX) * scaleX;
      const dy = (e.clientY - dragStart.current.clientY) * scaleY;
      setPan({
        x: dragStart.current.panX + dx,
        y: dragStart.current.panY + dy,
      });
    };

    const handleMouseUp = () => {
      setIsDragging(false);
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging, width, height]);

  // 滚轮缩放（以鼠标光标为锚点）
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
      if (!svgRef.current) return;
      const rect = svgRef.current.getBoundingClientRect();
      const mx = ((e.clientX - rect.left) / rect.width) * width;
      const my = ((e.clientY - rect.top) / rect.height) * height;

      const factor = e.deltaY < 0 ? 1.12 : 0.89;
      setZoom((prevZoom) => {
        const nextZoom = Math.min(Math.max(prevZoom * factor, 0.35), 3.5);
        setPan((prevPan) => {
          const worldX = (mx - prevPan.x) / prevZoom;
          const worldY = (my - prevPan.y) / prevZoom;
          return {
            x: mx - worldX * nextZoom,
            y: my - worldY * nextZoom,
          };
        });
        return nextZoom;
      });
    };

    container.addEventListener("wheel", handleWheel, { passive: false });
    return () => container.removeEventListener("wheel", handleWheel);
  }, [width, height]);

  // HUD 控制
  const handleZoomIn = () => {
    const nextZoom = Math.min(zoom * 1.25, 3.5);
    const mx = width / 2;
    const my = height / 2;
    const worldX = (mx - pan.x) / zoom;
    const worldY = (my - pan.y) / zoom;
    setZoom(nextZoom);
    setPan({ x: mx - worldX * nextZoom, y: my - worldY * nextZoom });
  };

  const handleZoomOut = () => {
    const nextZoom = Math.max(zoom * 0.8, 0.35);
    const mx = width / 2;
    const my = height / 2;
    const worldX = (mx - pan.x) / zoom;
    const worldY = (my - pan.y) / zoom;
    setZoom(nextZoom);
    setPan({ x: mx - worldX * nextZoom, y: my - worldY * nextZoom });
  };

  const handleReset = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  const hoveredNode = useMemo(
    () => data.nodes.find((n) => n.key === hoveredNodeKey) ?? null,
    [data.nodes, hoveredNodeKey],
  );

  return (
    <div
      ref={containerRef}
      style={{
        position: "relative",
        width: "100%",
        userSelect: isDragging ? "none" : undefined,
      }}
      onMouseDown={handleMouseDown}
    >
      <svg
        ref={svgRef}
        id="graph-canvas"
        width="100%"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={t("graph.canvasLabel")}
        style={{
          background: "var(--stage-card-subtle)",
          borderRadius: 16,
          display: "block",
          cursor: isDragging ? "grabbing" : "grab",
        }}
      >
        {/* ThreeUI 风格科技矩阵网格背景 */}
        <defs>
          <pattern id="kn-grid-pattern" width="32" height="32" patternUnits="userSpaceOnUse">
            <circle cx="16" cy="16" r="1" fill="rgba(128, 128, 128, 0.12)" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#kn-grid-pattern)" pointerEvents="none" />

        {/* 缩放与平移视口层 */}
        <g
          id="graph-viewport"
          transform={zoom === 1 && pan.x === 0 && pan.y === 0 ? undefined : `translate(${pan.x}, ${pan.y}) scale(${zoom})`}
        >
          {/* 边渲染 */}
          {data.edges.map((edge) => {
            const from = positions.get(edge.source);
            const to = positions.get(edge.target);
            if (!from || !to) return null;
            const dx = to.x - from.x;
            const dy = to.y - from.y;
            const length = Math.hypot(dx, dy);
            const angle = (Math.atan2(dy, dx) * 180) / Math.PI;

            const isEdgeSelected = selectedEdgeKey === edge.key;
            const isEdgeHovered = hoveredEdgeKey === edge.key;
            const isConnectedToActiveNode = activeNodeKey
              ? edge.source === activeNodeKey || edge.target === activeNodeKey
              : false;
            const isHighlighted = isEdgeSelected || isEdgeHovered || isConnectedToActiveNode;
            const isDimmed = hasActiveFilter && !isHighlighted;

            const strokeColor = EDGE_COLOR[edge.kind] ?? "var(--text-tertiary)";
            const baseWidth = edge.kind === "conflicts_with" ? 3.5 : 1.5;

            return (
              <g
                key={edge.key}
                data-edge-key={edge.key}
                data-kind={edge.kind}
                data-status={edge.status}
                strokeDasharray={edge.status === "pending" ? "5 4" : undefined}
                style={{
                  cursor: "pointer",
                  opacity: isDimmed ? 0.12 : isHighlighted ? 1.0 : 0.75,
                  transition: "opacity 0.2s ease",
                }}
                onClick={() => onSelectEdge(edge)}
                onMouseEnter={() => setHoveredEdgeKey(edge.key)}
                onMouseLeave={() => setHoveredEdgeKey(null)}
              >
                {/* 选中/悬浮高亮外发光 */}
                {isHighlighted && (
                  <line
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    stroke={strokeColor}
                    strokeWidth={baseWidth + 5}
                    opacity={0.38}
                  />
                )}
                <line
                  x1={from.x}
                  y1={from.y}
                  x2={to.x}
                  y2={to.y}
                  stroke={strokeColor}
                  strokeWidth={isHighlighted ? baseWidth + 1.2 : baseWidth}
                  strokeDasharray={edge.status === "pending" ? "5 4" : undefined}
                />
                {/* 易点击透明区域 */}
                <rect
                  x={from.x}
                  y={from.y - 10}
                  width={length}
                  height={20}
                  transform={`rotate(${angle} ${from.x} ${from.y})`}
                  fill="transparent"
                />
              </g>
            );
          })}

          {/* 节点渲染 */}
          {data.nodes.map((node) => {
            const point = positions.get(node.key);
            if (!point) return null;

            const isSelected = selectedNodeKey === node.key;
            const isHovered = hoveredNodeKey === node.key;
            const isConnectedToActive = activeConn?.neighborNodes.has(node.key) ?? false;
            const isDimmed = hasActiveFilter && !isConnectedToActive && !isSelected && !isHovered;

            const isControl = node.kind === "control";
            const mainColor = node.is_gap
              ? "var(--accent-ruby)"
              : isControl
              ? "var(--accent-blue)"
              : "var(--accent-emerald)";

            // thinLabels 为 true 时彻底隐藏编号（呈现极简纯净科技微点）；未勾选 thinLabels 时清晰展示编号
            const isThinned = thinLabels ?? (labelLimit !== undefined);
            const showLabel = !isThinned;
            const nodeRadius = isControl ? (isHovered ? 7.5 : 5.5) : (isHovered ? 6.5 : 4.8);
            const ringRadius = nodeRadius + (isHovered || isSelected ? 5 : 3.5);
            const labelX = ringRadius + 7;
            const labelWidth = node.code.length * 7.5 + 4;
            const totalWidth = showLabel ? labelX + labelWidth + 10 : 36;

            return (
              <g
                key={node.key}
                data-node-key={node.key}
                data-kind={node.kind}
                data-gap={node.is_gap ? "true" : "false"}
                transform={`translate(${point.x},${point.y})`}
                style={{
                  cursor: "pointer",
                  opacity: isDimmed ? 0.22 : 1.0,
                  transition: "opacity 0.2s ease",
                }}
                onClick={() => onSelectNode(node)}
                onMouseEnter={(e) => {
                  setHoveredNodeKey(node.key);
                  if (containerRef.current) {
                    const rect = containerRef.current.getBoundingClientRect();
                    setTooltipPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
                  }
                }}
                onMouseMove={(e) => {
                  if (containerRef.current) {
                    const rect = containerRef.current.getBoundingClientRect();
                    setTooltipPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
                  }
                }}
                onMouseLeave={() => {
                  setHoveredNodeKey(null);
                  setTooltipPos(null);
                }}
              >
                <g
                  style={{
                    transform: isHovered ? "scale(1.25)" : isSelected ? "scale(1.15)" : "scale(1)",
                    transition: "transform 0.2s cubic-bezier(0.16, 1, 0.3, 1)",
                  }}
                >
                  {/* 易点击透明区域 (Hitbox)：带编号时覆盖整个微点与文字区域，隐藏编号时覆盖圆形区域 */}
                  {showLabel ? (
                    <rect
                      x={-18}
                      y={-14}
                      width={totalWidth}
                      height={28}
                      rx={14}
                      fill="transparent"
                    />
                  ) : (
                    <circle r={18} fill="transparent" />
                  )}

                  {/* 悬停/选中发光外光晕 */}
                  {(isHovered || isSelected) && (
                    <circle
                      r={ringRadius + 5}
                      fill={mainColor}
                      opacity={0.25}
                    />
                  )}

                  {/* 精密外层轨道环 */}
                  <circle
                    r={ringRadius}
                    fill="var(--stage-card)"
                    stroke={isSelected || isHovered ? mainColor : "var(--stage-border)"}
                    strokeWidth={isSelected || isHovered ? 1.5 : 1}
                    strokeDasharray={node.is_gap ? "2 1.5" : undefined}
                  />

                  {/* 核心纯色微宝石点 */}
                  <circle
                    r={nodeRadius}
                    fill={mainColor}
                  />

                  {/* 差距节点中心微警示点 */}
                  {node.is_gap && (
                    <circle
                      r={1.5}
                      fill="#ffffff"
                    />
                  )}

                  {/* 选中时同心微虚线外圈 */}
                  {isSelected && (
                    <circle
                      r={ringRadius + 3}
                      fill="none"
                      stroke={mainColor}
                      strokeWidth={1}
                      strokeDasharray="2 2"
                      opacity={0.7}
                    />
                  )}

                  {/* 节点编号文字：全部图表下未勾选 thin label 时清晰呈现，勾选后视觉完全隐藏保持极简美观 */}
                  {labelled.has(node.key) && (
                    <text
                      data-node-label={node.key}
                      x={showLabel ? labelX : 0}
                      y={showLabel ? 3.5 : 0}
                      textAnchor={showLabel ? "start" : "middle"}
                      dominantBaseline={showLabel ? undefined : "central"}
                      fontSize={11}
                      fontWeight={isSelected || isHovered ? 600 : 500}
                      fontFamily="'SF Mono', Menlo, Monaco, Consolas, monospace"
                      fill={isSelected || isHovered ? "var(--text-primary)" : "var(--text-secondary)"}
                      opacity={showLabel ? 1 : 0}
                      style={{
                        userSelect: "none",
                        pointerEvents: showLabel ? undefined : "none",
                        transition: "opacity 0.2s ease, fill 0.2s ease",
                      }}
                    >
                      {node.code}
                    </text>
                  )}
                </g>
              </g>
            );
          })}
        </g>
      </svg>

      {/* ThreeUI 风格浮动毛玻璃 HUD 控制栏 */}
      <div
        style={{
          position: "sticky",
          bottom: 14,
          display: "flex",
          justifyContent: "flex-end",
          pointerEvents: "none",
          zIndex: 10,
          padding: "0 14px 14px 0",
          marginTop: -50,
        }}
      >
        <div className="kn-canvas-hud" style={{ pointerEvents: "auto", position: "relative", bottom: "auto", right: "auto" }}>
          <button
            type="button"
            className="kn-hud-btn"
            onClick={handleZoomOut}
            aria-label={t("graph.zoomOut")}
            title={t("graph.zoomOut")}
          >
            −
          </button>
          <span className="kn-hud-badge" title={t("graph.zoomLevel")}>
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            className="kn-hud-btn"
            onClick={handleZoomIn}
            aria-label={t("graph.zoomIn")}
            title={t("graph.zoomIn")}
          >
            +
          </button>
          <span style={{ width: 1, height: 14, background: "var(--stage-border)", margin: "0 2px" }} />
          <button
            type="button"
            className="kn-hud-btn"
            onClick={handleReset}
            aria-label={t("graph.resetView")}
            title={t("graph.resetView")}
          >
            ⟲
          </button>
        </div>
      </div>

      {/* ThreeUI 风格悬浮毛玻璃微型气泡提示 (Micro-Tooltip) */}
      {hoveredNode && tooltipPos && (
        <div
          className="kn-node-tooltip"
          style={{
            left: tooltipPos.x,
            top: tooltipPos.y,
          }}
        >
          <div className="kn-node-tooltip-code">{hoveredNode.code}</div>
          <div className="kn-node-tooltip-title">{hoveredNode.title}</div>
          <div className="kn-node-tooltip-meta">
            <span>
              {hoveredNode.kind === "control"
                ? t("graph.drawer.control")
                : t("graph.drawer.frameworkItem")}
            </span>
            <span>•</span>
            <span>
              {nodeConnections.get(hoveredNode.key)?.total ?? 0} {t("graph.relationsCount")}
            </span>
          </div>
          {hoveredNode.is_gap && (
            <div style={{ marginTop: 6 }}>
              <span className="kn-badge kn-badge-ruby" style={{ fontSize: "0.6875rem", padding: "2px 6px" }}>
                {t("graph.drawer.notCovered")}
              </span>
            </div>
          )}
          {hoveredNode.pending_edges > 0 && (
            <div style={{ marginTop: 4 }}>
              <span className="kn-badge kn-badge-amber" style={{ fontSize: "0.6875rem", padding: "2px 6px" }}>
                {t("graph.pendingCount", { count: hoveredNode.pending_edges })}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
