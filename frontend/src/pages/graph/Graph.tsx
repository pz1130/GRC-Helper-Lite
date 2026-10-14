import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { request } from "../../api";
import { useControlSearch, withSelected } from "../../controlSearch";
import type { ControlOption } from "../../controlSearch";
import { hasText } from "../../i18n";
import { Canvas } from "./Canvas";
import { Drawer } from "./Drawer";
import { downloadGraph } from "./export";
import { canvasHeight, MIN_CANVAS_HEIGHT, type LayoutKind } from "./layout";
import type { GraphData, GraphEdgeData, GraphNodeData } from "./types";
import { LoadingState } from "../../components/ui";
import { useViewMode, ViewToggle } from "../../three/ViewToggle";

// 3D 视图单独分包：只有切到 3D 才下载 three.js。
const Graph3D = lazy(() => import("../../three/Graph3D"));

const WIDTH = 960;

export function Graph() {
  const { t } = useTranslation();
  const [view, setView] = useState<"relations" | "mappings">("relations");
  const [layoutKind, setLayoutKind] = useState<LayoutKind>("document");
  const [viewMode, setViewMode] = useViewMode("grc.graph.view");
  const [selectedNode, setSelectedNode] = useState<GraphNodeData | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdgeData | null>(null);
  const [focus, setFocus] = useState<string>("");
  const [controlQuery, setControlQuery] = useState("");
  // 聚焦中的控制点换了搜索词也要留在下拉里，否则选择框会跳回「全景」的样子。
  const [focusedControl, setFocusedControl] = useState<ControlOption | null>(null);
  const [hops, setHops] = useState<number>(1);
  const [includePending, setIncludePending] = useState(false);
  const [onlyUnconfirmed, setOnlyUnconfirmed] = useState(false);
  const [onlyConflicts, setOnlyConflicts] = useState(false);
  const [frameworkId, setFrameworkId] = useState<string>("");
  const [thinLabels, setThinLabels] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const canvasWrapper = useRef<HTMLDivElement>(null);

  const exportGraph = async (format: "svg" | "png") => {
    const svg = canvasWrapper.current?.querySelector("svg");
    if (!svg) return;
    setExportError(null);
    try {
      await downloadGraph(svg as SVGSVGElement, format);
    } catch (error) {
      // 导出失败必须说出来。从前这里什么都不做，用户看到的就是点了没反应。
      setExportError(error instanceof Error ? error.message : "graph.exportFailed");
    }
  };

  const params = new URLSearchParams();
  if (focus) params.set("focus", focus);
  params.set("hops", String(hops));
  if (includePending) params.set("include_pending", "true");
  if (onlyConflicts) params.set("types", "conflicts_with");
  if (frameworkId) params.set("framework_id", frameworkId);

  const { data: documents } = useQuery({
    queryKey: ["graph", "documents"],
    queryFn: () => request<{ id: number; title: string }[]>("/api/documents"),
  });
  const controlSearch = useControlSearch(controlQuery);
  const controls = withSelected(controlSearch.data, focus.startsWith("control:") ? focusedControl : null);
  const { data: frameworks } = useQuery({
    queryKey: ["graph", "frameworks"],
    queryFn: () => request<{ id: number; name_en: string }[]>("/api/frameworks"),
  });

  const activeFrameworkId = frameworkId || (frameworks?.[0]?.id ? String(frameworks[0].id) : "");
  const mappingParams = new URLSearchParams();
  mappingParams.set("framework_id", activeFrameworkId);
  if (focus) mappingParams.set("focus", focus);
  mappingParams.set("hops", String(hops));
  if (includePending) mappingParams.set("include_pending", "true");

  const { data: raw, isLoading, error } = useQuery({
    queryKey: ["graph", view, view === "relations" ? params.toString() : mappingParams.toString()],
    queryFn: () =>
      request<GraphData>(
        view === "relations"
          ? `/api/graph/relations?${params.toString()}`
          : `/api/graph/mappings?${mappingParams.toString()}`,
      ),
    enabled: view === "relations" || activeFrameworkId !== "",
  });

  const data: GraphData | undefined = raw && onlyUnconfirmed
    ? (() => {
        const edges = raw.edges.filter((edge) => edge.status === "pending");
        const kept = new Set(edges.flatMap((edge) => [edge.source, edge.target]));
        const nodes = raw.nodes.filter((node) => kept.has(node.key));
        return {
          ...raw,
          edges,
          nodes,
          stats: { ...raw.stats, nodes: nodes.length, edges: edges.length },
        };
      })()
    : raw;

  const activeLayout: LayoutKind = view === "mappings" ? "bipartite" : layoutKind;
  const height = data ? canvasHeight(activeLayout, data.nodes) : MIN_CANVAS_HEIGHT;

  return (
    <section>
      <h1>{t("graph.title")}</h1>

      {/* 视图与布局切换区：独立醒目行 */}
      <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap", margin: "16px 0 14px" }}>
        <div className="kn-row">
          <span style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            {t("graph.viewLabel")}:
          </span>
          <div className="kn-segmented" style={{ padding: "4px" }}>
            {(["relations", "mappings"] as const).map((kind) => (
              <button
                key={kind}
                type="button"
                className={`kn-segmented-item ${view === kind ? "active" : ""}`}
                aria-pressed={view === kind}
                onClick={() => setView(kind)}
                style={{ padding: "6px 18px", fontSize: "0.875rem" }}
              >
                {t(`graph.view.${kind}`)}
              </button>
            ))}
          </div>
        </div>

        {view === "relations" && (
          <div className="kn-row">
            <span style={{ fontSize: "0.8125rem", color: "var(--text-secondary)", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
              {t("graph.layoutLabel")}:
            </span>
            <div className="kn-segmented" style={{ padding: "4px" }}>
              {(["document", "function", "force"] as LayoutKind[]).map((kind) => (
                <button
                  key={kind}
                  type="button"
                  className={`kn-segmented-item ${layoutKind === kind ? "active" : ""}`}
                  aria-pressed={layoutKind === kind}
                  onClick={() => setLayoutKind(kind)}
                  style={{ padding: "6px 16px", fontSize: "0.875rem" }}
                >
                  {t(`graph.layout.${kind}`)}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* 过滤器与控制台 */}
      <div className="kn-graph-toolbar">
        <label className="kn-inline-label">
          <span className="kn-secondary kn-text-xs kn-medium">{t("graph.focus")}</span>
          <select
            value={focus}
            onChange={(event) => {
              setFocus(event.target.value);
              setFocusedControl(controls.find((control) => `control:${control.id}` === event.target.value) ?? null);
            }} className="kn-select-sm kn-text-xs"
          >
            <option value="">{t("graph.panorama")}</option>
            {documents?.map((document) => (
              <option key={`document:${document.id}`} value={`document:${document.id}`}>
                {document.title}
              </option>
            ))}
            {controls.map((control) => (
              <option key={`control:${control.id}`} value={`control:${control.id}`}>
                {control.code}
              </option>
            ))}
          </select>
        </label>
        <input
          type="search"
          aria-label={t("graph.searchControls")}
          placeholder={t("graph.searchControlsPlaceholder")}
          value={controlQuery}
          onChange={(event) => setControlQuery(event.target.value)}
          style={{ padding: "6px 10px", fontSize: "0.8125rem", width: 180 }}
        />
        <label className="kn-inline-label">
          <span className="kn-secondary kn-text-xs kn-medium">{t("graph.hops")}</span>
          <select value={hops} onChange={(event) => setHops(Number(event.target.value))} className="kn-select-sm kn-text-xs">
            <option value={1}>1</option>
            <option value={2}>2</option>
          </select>
        </label>
        <label className="kn-filter-chip">
          <input type="checkbox" checked={includePending} onChange={(event) => setIncludePending(event.target.checked)} />
          {t("graph.filters.pending")}
        </label>
        <label className="kn-filter-chip">
          <input
            type="checkbox"
            checked={onlyUnconfirmed}
            onChange={(event) => {
              const checked = event.target.checked;
              setOnlyUnconfirmed(checked);
              if (checked) setIncludePending(true);
            }}
          />
          {t("graph.filters.onlyUnconfirmed")}
        </label>
        <label className="kn-filter-chip">
          <input type="checkbox" checked={onlyConflicts} onChange={(event) => setOnlyConflicts(event.target.checked)} />
          {t("graph.filters.onlyConflicts")}
        </label>
        <label className="kn-inline-label">
          <span className="kn-secondary kn-text-xs kn-medium">{t("graph.filters.framework")}</span>
          <select value={frameworkId} onChange={(event) => setFrameworkId(event.target.value)} className="kn-select-sm kn-text-xs">
            <option value="">{t("graph.filters.allFrameworks")}</option>
            {frameworks?.map((framework) => (
              <option key={framework.id} value={framework.id}>{framework.name_en}</option>
            ))}
          </select>
        </label>
        <label className="kn-filter-chip">
          <input type="checkbox" checked={thinLabels} onChange={(event) => setThinLabels(event.target.checked)} />
          {t("graph.thinLabels")}
        </label>
      </div>

      {isLoading && <LoadingState />}
      {/* 没有这一行时，加载失败只是「加载中」消失、画布不出现，看起来像图是空的 */}
      {error && <p role="alert" className="kn-danger-text">{error.message}</p>}
      {data && (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12, margin: "14px 0 12px" }}>
            <p style={{ color: "var(--text-secondary)", fontSize: "0.875rem", margin: 0, fontWeight: 500 }}>
              {t("graph.stats", { nodes: data.stats.nodes, edges: data.stats.edges })}
              {data.stats.pending_edges > 0 && ` · ${t("graph.pendingCount", { count: data.stats.pending_edges })}`}
              {data.stats.truncated && ` · ${t("graph.truncated")}`}
              {view === "mappings" && ` · ${t("graph.gapCount", { count: data.nodes.filter((node) => node.is_gap).length })}`}
            </p>
            <div className="kn-row">
              <ViewToggle mode={viewMode} onChange={setViewMode} />
              <button type="button" className="kn-btn-secondary" style={{ padding: "6px 14px", fontSize: "0.8125rem" }} onClick={() => exportGraph("svg")}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="kn-mr-1"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                {t("graph.exportSvg")}
              </button>
              <button type="button" className="kn-btn-secondary" style={{ padding: "6px 14px", fontSize: "0.8125rem" }} onClick={() => exportGraph("png")}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="kn-mr-1"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                {t("graph.exportPng")}
              </button>
              {exportError && (
                <p role="alert" style={{ margin: 0, alignSelf: "center", color: "var(--accent-ruby)", fontSize: "0.8125rem" }}>
                  {/* hasText 而不是 defaultValue：缺失键的 handler 优先级更高，浏览器自己抛的
                      异常消息（以句点结尾）会被取最后一段，显示成「—」。 */}
                  {hasText(exportError) ? t(exportError) : t("graph.exportFailed")}
                </p>
              )}
            </div>
          </div>

          <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
            {viewMode === "3d" ? (
              <div className="kn-graph-canvas-box kn-graph-canvas-box--3d">
                <Suspense fallback={<LoadingState />}>
                  <Graph3D
                    data={data}
                    height={MIN_CANVAS_HEIGHT}
                    selectedNodeKey={selectedNode?.key}
                    onSelectNode={(node) => {
                      setSelectedEdge(null);
                      setSelectedNode(node);
                    }}
                  />
                </Suspense>
                <p className="g3d-hint">{t("view3d.hint")}</p>
              </div>
            ) : (
            <div ref={canvasWrapper} className="kn-graph-canvas-box" style={{ flex: 1, minWidth: 0, overflow: "auto", maxHeight: MIN_CANVAS_HEIGHT }}>
              <Canvas
                data={data}
                layoutKind={activeLayout}
                width={WIDTH}
                height={height}
                selectedNodeKey={selectedNode?.key}
                selectedEdgeKey={selectedEdge?.key}
                thinLabels={thinLabels}
                labelLimit={thinLabels ? Math.max(1, Math.round(data.nodes.length * 0.15)) : undefined}
                onSelectNode={(node) => {
                  setSelectedEdge(null);
                  setSelectedNode(node);
                }}
                onSelectEdge={(edge) => {
                  setSelectedNode(null);
                  setSelectedEdge(edge);
                }}
              />
            </div>
            )}
            <Drawer
              node={selectedNode}
              edge={selectedEdge}
              onClose={() => {
                setSelectedNode(null);
                setSelectedEdge(null);
              }}
            />
          </div>

          {/* 图例与分组栏 */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12, marginTop: 14 }}>
            <ul style={{ display: "flex", gap: 10, flexWrap: "wrap", listStyle: "none", padding: 0, margin: 0 }}>
              {data.groups.map((group) => (
                <li key={group.key} className="kn-badge" style={{ fontSize: "0.75rem", padding: "4px 12px", background: "var(--stage-card-subtle)" }}>
                  {group.label}
                </li>
              ))}
            </ul>

            <div className="kn-graph-legend">
              {view === "relations" ? (
                <>
                  <span className="kn-graph-legend-item">
                    <span className="kn-graph-legend-line" style={{ background: "var(--accent-blue)" }} />
                    {t("graph.edgeKind.depends_on")}
                  </span>
                  <span className="kn-graph-legend-item">
                    <span className="kn-graph-legend-line" style={{ background: "var(--accent-purple)" }} />
                    {t("graph.edgeKind.duplicates")}
                  </span>
                  <span className="kn-graph-legend-item">
                    <span className="kn-graph-legend-line" style={{ background: "var(--accent-ruby)", height: 3 }} />
                    {t("graph.edgeKind.conflicts_with")}
                  </span>
                </>
              ) : (
                <>
                  <span className="kn-graph-legend-item">
                    <span className="kn-graph-legend-line" style={{ background: "var(--accent-emerald)" }} />
                    {t("graph.edgeKind.full")}
                  </span>
                  <span className="kn-graph-legend-item">
                    <span className="kn-graph-legend-line" style={{ background: "var(--accent-cyan)" }} />
                    {t("graph.edgeKind.partial")}
                  </span>
                  <span className="kn-graph-legend-item">
                    <span className="kn-graph-legend-line" style={{ background: "var(--accent-amber)" }} />
                    {t("graph.edgeKind.supporting")}
                  </span>
                  <span className="kn-graph-legend-item">
                    <span className="kn-dot kn-dot-ruby" style={{ width: 8, height: 8 }} />
                    {t("graph.drawer.notCovered")}
                  </span>
                </>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
}
