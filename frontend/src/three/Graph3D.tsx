// 知识图谱的 3D 视图（按需加载的分包）。布局是自带的一个小型三维力导向：
// 节点数在几百以内，O(n²) 斥力足够，不值得为它再引一个图库。
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";

import type { GraphData, GraphEdgeData, GraphNodeData } from "../pages/graph/types";
import { createScene, themeColor } from "./scene";
import { prefersReducedMotion } from "./support";

const EDGE_VAR: Record<string, [string, string]> = {
  depends_on: ["--accent-blue", "#5e6ad2"],
  duplicates: ["--accent-purple", "#8b5cf6"],
  conflicts_with: ["--accent-ruby", "#e11d48"],
  full: ["--accent-emerald", "#059669"],
  partial: ["--accent-cyan", "#0891b2"],
  supporting: ["--accent-amber", "#d97706"],
};

interface Body { node: GraphNodeData; p: THREE.Vector3; v: THREE.Vector3 }

function simulate(bodies: Body[], edges: [number, number][], steps: number) {
  const repulse = 900;
  const spring = 0.02;
  const rest = 38;
  const tmp = new THREE.Vector3();
  for (let s = 0; s < steps; s++) {
    for (let i = 0; i < bodies.length; i++) {
      for (let j = i + 1; j < bodies.length; j++) {
        tmp.subVectors(bodies[i].p, bodies[j].p);
        const d2 = Math.max(tmp.lengthSq(), 1);
        tmp.multiplyScalar(repulse / (d2 * Math.sqrt(d2)));
        bodies[i].v.add(tmp);
        bodies[j].v.sub(tmp);
      }
    }
    for (const [a, b] of edges) {
      tmp.subVectors(bodies[b].p, bodies[a].p);
      const d = tmp.length() || 1;
      tmp.multiplyScalar(((d - rest) * spring) / d);
      bodies[a].v.add(tmp);
      bodies[b].v.sub(tmp);
    }
    for (const body of bodies) {
      body.v.addScaledVector(body.p, -0.004); // 向心，防止孤立点飘走
      body.v.multiplyScalar(0.82);
      body.p.add(body.v);
    }
  }
}

export default function Graph3D({
  data,
  height,
  onSelectNode,
  selectedNodeKey,
}: {
  data: GraphData;
  height: number;
  onSelectNode: (node: GraphNodeData) => void;
  onSelectEdge?: (edge: GraphEdgeData) => void;
  selectedNodeKey?: string | null;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const labelsRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ node: GraphNodeData; x: number; y: number } | null>(null);
  const selectRef = useRef(onSelectNode);
  selectRef.current = onSelectNode;

  useEffect(() => {
    const host = hostRef.current;
    const labelHost = labelsRef.current;
    if (!host || !labelHost) return;
    const kit = createScene(host, { autoRotate: true, cameraPos: [0, 40, 260] });

    const index = new Map<string, number>();
    const bodies: Body[] = data.nodes.map((node, i) => {
      index.set(node.key, i);
      const r = 60 + Math.random() * 40;
      const a = (i / data.nodes.length) * Math.PI * 2;
      return { node, p: new THREE.Vector3(Math.cos(a) * r, (Math.random() - 0.5) * 60, Math.sin(a) * r), v: new THREE.Vector3() };
    });
    const links = data.edges
      .map((edge) => [index.get(edge.source), index.get(edge.target), edge] as const)
      .filter((l): l is readonly [number, number, GraphEdgeData] => l[0] !== undefined && l[1] !== undefined);
    simulate(bodies, links.map(([a, b]) => [a, b]), 260);

    const controlColor = themeColor("--accent-blue", "#5e6ad2");
    const itemColor = themeColor("--accent-emerald", "#059669");
    const gapColor = themeColor("--accent-ruby", "#e11d48");
    const geo = new THREE.SphereGeometry(1, 24, 16);
    const meshes = bodies.map((body) => {
      const color = body.node.is_gap ? gapColor : body.node.kind === "control" ? controlColor : itemColor;
      const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color, roughness: 0.35, metalness: 0.1, emissive: color, emissiveIntensity: kit.isDark ? 0.25 : 0.05 }));
      const size = 3.2 + Math.min(4, body.node.pending_edges);
      mesh.scale.setScalar(body.node.key === selectedNodeKey ? size * 1.5 : size);
      mesh.position.copy(body.p);
      mesh.userData.index = bodies.indexOf(body);
      kit.scene.add(mesh);
      return mesh;
    });

    const positions = new Float32Array(links.length * 6);
    const colors = new Float32Array(links.length * 6);
    links.forEach(([, , edge], i) => {
      const [name, fb] = EDGE_VAR[edge.kind] ?? ["--text-tertiary", "#8a8d94"];
      const c = themeColor(name, fb);
      const fade = edge.status === "pending" ? 0.45 : 1;
      for (let k = 0; k < 2; k++) colors.set([c.r * fade, c.g * fade, c.b * fade], i * 6 + k * 3);
    });
    const lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    lineGeo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    const lines = new THREE.LineSegments(lineGeo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.8 }));
    kit.scene.add(lines);

    // 标签：只给前 80 个节点建 DOM，其余靠悬停提示。
    const labelEls = bodies.slice(0, 80).map((body) => {
      const el = document.createElement("span");
      el.className = "g3d-label";
      el.textContent = body.node.code;
      labelHost.appendChild(el);
      return el;
    });

    const live = !prefersReducedMotion();
    const v = new THREE.Vector3();
    kit.onFrame(() => {
      if (live) simulate(bodies, links.map(([a, b]) => [a, b]), 1);
      bodies.forEach((body, i) => meshes[i].position.copy(body.p));
      links.forEach(([a, b], i) => {
        positions.set([bodies[a].p.x, bodies[a].p.y, bodies[a].p.z, bodies[b].p.x, bodies[b].p.y, bodies[b].p.z], i * 6);
      });
      lineGeo.attributes.position.needsUpdate = true;
      const w = host.clientWidth;
      const h = host.clientHeight;
      labelEls.forEach((el, i) => {
        v.copy(bodies[i].p).project(kit.camera);
        const hidden = v.z > 1;
        el.style.transform = `translate(${((v.x + 1) / 2) * w + 8}px, ${((1 - v.y) / 2) * h - 8}px)`;
        el.style.opacity = hidden ? "0" : String(Math.max(0.35, 1 - (v.z - 0.9) * 8));
      });
    });

    const ray = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    const pick = (e: PointerEvent) => {
      const rect = kit.renderer.domElement.getBoundingClientRect();
      ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
      ray.setFromCamera(ndc, kit.camera);
      const hit = ray.intersectObjects(meshes)[0];
      return hit ? { body: bodies[hit.object.userData.index as number], x: e.clientX - rect.left, y: e.clientY - rect.top } : null;
    };
    const onMove = (e: PointerEvent) => {
      const hit = pick(e);
      kit.renderer.domElement.style.cursor = hit ? "pointer" : "grab";
      setHover(hit ? { node: hit.body.node, x: hit.x, y: hit.y } : null);
    };
    let downAt = { x: 0, y: 0 };
    const onDown = (e: PointerEvent) => (downAt = { x: e.clientX, y: e.clientY });
    const onUp = (e: PointerEvent) => {
      // 拖动旋转后松手不算点击
      if (Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) > 4) return;
      const hit = pick(e);
      if (hit) selectRef.current(hit.body.node);
    };
    const el = kit.renderer.domElement;
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);
    return () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointerup", onUp);
      labelEls.forEach((label) => label.remove());
      geo.dispose();
      kit.dispose();
    };
  }, [data, selectedNodeKey]);

  return (
    <div className="g3d" style={{ height }}>
      <div ref={hostRef} className="g3d-canvas" />
      <div ref={labelsRef} className="g3d-labels" aria-hidden="true" />
      {hover ? (
        <div className="kn-node-tooltip g3d-tooltip" style={{ left: hover.x, top: hover.y }}>
          <div className="kn-node-tooltip-code">{hover.node.code}</div>
          <div className="kn-node-tooltip-title">{hover.node.title}</div>
        </div>
      ) : null}
    </div>
  );
}
