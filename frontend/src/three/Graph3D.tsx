// 知识图谱的 3D 视图（按需加载的分包）。
// 布局：自带的小型三维力导向，一次性算完再展示——节点数几百以内，O(n²) 足够，
// 也免得画面一直「抖」。画质要点见 scene.ts；这里负责节点、曲线连线、流动粒子与标签。
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import * as THREE from "three";
import { Line2 } from "three/examples/jsm/lines/Line2.js";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { LineGeometry } from "three/examples/jsm/lines/LineGeometry.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";

import type { GraphData, GraphEdgeData, GraphNodeData } from "../pages/graph/types";
import { useTheme } from "../theme";
import { contactShadow, createFloor, createScene, declutterLabels, richen, themeColor } from "./scene";
import { FloatingTip } from "./FloatingTip";

const EDGE_VAR: Record<string, [string, string]> = {
  depends_on: ["--accent-blue", "#5e6ad2"],
  duplicates: ["--accent-purple", "#8b5cf6"],
  conflicts_with: ["--accent-ruby", "#e11d48"],
  full: ["--accent-emerald", "#059669"],
  partial: ["--accent-cyan", "#0891b2"],
  supporting: ["--accent-amber", "#d97706"],
};

// 视觉类别只在 3D 视图里推导，不改接口：缺口（is_gap）视为风险，framework_item 为框架条款，
// 其余为控制项；「制度」来自 document 分组，作为枢纽节点额外画出，把同一份制度下的节点串起来。
type Category = "control" | "framework" | "risk" | "policy";
const CATEGORY: Record<Category, { var: string; fb: string }> = {
  control: { var: "--accent-blue", fb: "#5e6ad2" },
  framework: { var: "--accent-emerald", fb: "#059669" },
  risk: { var: "--accent-ruby", fb: "#e11d48" },
  policy: { var: "--accent-amber", fb: "#d97706" },
};
const categoryOf = (node: GraphNodeData): Category => (node.is_gap ? "risk" : node.kind === "framework_item" ? "framework" : "control");

function geometryFor(cat: Category): THREE.BufferGeometry {
  // 形状区分类别（色弱用户也能分辨）：球 = 控制项，圆角方块 = 框架条款，八面体 = 风险缺口，六棱柱 = 制度。
  if (cat === "framework") return new RoundedBoxGeometry(1.6, 1.6, 1.6, 4, 0.32);
  if (cat === "risk") return new THREE.OctahedronGeometry(1.25, 0);
  if (cat === "policy") return new THREE.CylinderGeometry(1.15, 1.15, 0.9, 6, 1);
  return new THREE.SphereGeometry(1, 48, 32);
}

interface Body { node: GraphNodeData; p: THREE.Vector3; v: THREE.Vector3 }

function layout3d(nodes: GraphNodeData[], edges: [number, number][]): THREE.Vector3[] {
  // 确定性的初始位置（黄金角螺旋），同一份数据每次打开形状一致。
  const bodies: Body[] = nodes.map((node, i) => {
    const y = 1 - (i / Math.max(1, nodes.length - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const a = i * 2.39996;
    const R = 40 + nodes.length * 2;
    return { node, p: new THREE.Vector3(Math.cos(a) * r * R, y * R * 0.6, Math.sin(a) * r * R), v: new THREE.Vector3() };
  });
  const tmp = new THREE.Vector3();
  for (let s = 0; s < 320; s++) {
    const cool = 1 - s / 320;
    for (let i = 0; i < bodies.length; i++) {
      for (let j = i + 1; j < bodies.length; j++) {
        tmp.subVectors(bodies[i].p, bodies[j].p);
        const d2 = Math.max(tmp.lengthSq(), 4);
        tmp.multiplyScalar(1400 / (d2 * Math.sqrt(d2)));
        bodies[i].v.add(tmp);
        bodies[j].v.sub(tmp);
      }
    }
    for (const [a, b] of edges) {
      tmp.subVectors(bodies[b].p, bodies[a].p);
      const d = tmp.length() || 1;
      tmp.multiplyScalar(((d - 42) * 0.03) / d);
      bodies[a].v.add(tmp);
      bodies[b].v.sub(tmp);
    }
    for (const body of bodies) {
      body.v.addScaledVector(body.p, -0.006);
      body.v.multiplyScalar(0.8 * cool + 0.1);
      body.p.add(body.v);
    }
  }
  // 整体抬到地面以上
  const minY = Math.min(...bodies.map((b) => b.p.y));
  bodies.forEach((b) => (b.p.y += 18 - minY));
  return bodies.map((b) => b.p);
}

function glowTexture(): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,0.9)");
  grad.addColorStop(0.35, "rgba(255,255,255,0.35)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
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
  const { theme } = useTheme();
  const { t } = useTranslation();
  const present = useMemo(() => {
    const set = new Set<Category>(data.nodes.map(categoryOf));
    if (data.groups.some((g) => g.kind === "document")) set.add("policy");
    return (["control", "framework", "risk", "policy"] as Category[]).filter((c) => set.has(c));
  }, [data]);
  const hostRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ node: GraphNodeData; x: number; y: number } | null>(null);
  const selectRef = useRef(onSelectNode);
  selectRef.current = onSelectNode;
  const selectedRef = useRef<string | null | undefined>(selectedNodeKey);
  const focusRef = useRef<(key: string | null | undefined) => void>(() => {});

  useEffect(() => {
    selectedRef.current = selectedNodeKey;
    focusRef.current(selectedNodeKey);
  }, [selectedNodeKey]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const kit = createScene(host, { autoRotate: true, intro: true, refined: true, cameraPos: [0, 110, 260], target: [0, 40, 0] });
    kit.controls.minDistance = 60;
    kit.controls.maxDistance = 700;
    kit.controls.maxPolarAngle = Math.PI * 0.49;

    const index = new Map<string, number>();
    data.nodes.forEach((node, i) => index.set(node.key, i));
    const links = data.edges
      .map((edge) => [index.get(edge.source), index.get(edge.target), edge] as const)
      .filter((l): l is readonly [number, number, GraphEdgeData] => l[0] !== undefined && l[1] !== undefined);
    const pos = layout3d(data.nodes, links.map(([a, b]) => [a, b]));
    const center = pos.reduce((acc, p) => acc.add(p), new THREE.Vector3()).divideScalar(Math.max(1, pos.length));
    kit.controls.target.copy(center);

    const floor = createFloor(900, 20, kit.isDark);
    kit.scene.add(floor);

    // ---------- 节点 ----------
    const geos = new Map<Category, THREE.BufferGeometry>();
    const geo = (c: Category) => geos.get(c) ?? geos.set(c, geometryFor(c)).get(c)!;
    const glowTex = glowTexture();
    const makeNode = (cat: Category, at: THREE.Vector3, base: number, code: string, title: string) => {
      const color = richen(themeColor(CATEGORY[cat].var, CATEGORY[cat].fb), kit.isDark);
      const material = new THREE.MeshPhysicalMaterial({
        color, roughness: cat === "policy" ? 0.32 : 0.24, metalness: cat === "framework" ? 0.25 : 0.05,
        clearcoat: 0.8, clearcoatRoughness: 0.22, envMapIntensity: kit.isDark ? 0.6 : 0.95,
        // 制度枢纽做成半透明「玻璃」，与实心节点区分层级
        transmission: cat === "policy" ? 0.35 : 0, thickness: 2, ior: 1.4,
        emissive: color, emissiveIntensity: 0,
      });
      const mesh = new THREE.Mesh(geo(cat), material);
      mesh.scale.setScalar(base);
      mesh.position.copy(at);
      mesh.castShadow = true;
      kit.scene.add(mesh);
      const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending }));
      halo.scale.setScalar(base * 5);
      halo.position.copy(at);
      kit.scene.add(halo);
      const shadow = contactShadow(base * 1.6, kit.isDark);
      shadow.position.set(at.x, 0.06, at.z);
      kit.scene.add(shadow);
      const el = document.createElement("div");
      el.className = `g3d-label g3d-label--${cat}`;
      el.innerHTML = `<i></i><b></b><span></span>`;
      el.children[1].textContent = code;
      el.children[2].textContent = title;
      const label = new CSS2DObject(el);
      label.position.copy(at).add(new THREE.Vector3(0, base + 3, 0));
      label.center.set(0.5, 1);
      kit.scene.add(label);
      return { cat, mesh, material, halo, el, label, base, emissive: 0, scale: base, spin: cat === "risk" || cat === "policy" };
    };
    const nodes = data.nodes.map((node, i) => {
      const n = makeNode(categoryOf(node), pos[i], 4.2 + Math.min(3, node.pending_edges * 0.8), node.code, node.title);
      n.mesh.userData.index = i;
      return { ...n, node };
    });

    // 制度枢纽：放在成员节点重心的正上方，用细淡线连到成员。
    const policyLinks: { a: THREE.Vector3; b: THREE.Vector3 }[] = [];
    const policies = data.groups
      .filter((g) => g.kind === "document")
      .map((g) => {
        const members = data.nodes.map((n, i) => (n.group === g.key || n.group_extra.includes(g.key) ? i : -1)).filter((i) => i >= 0);
        if (members.length < 2) return null;
        const at = members.reduce((acc, i) => acc.add(pos[i]), new THREE.Vector3()).divideScalar(members.length);
        at.y = Math.max(...members.map((i) => pos[i].y)) + 26;
        members.forEach((i) => policyLinks.push({ a: at, b: pos[i] }));
        return makeNode("policy", at, 5, g.label, "");
      })
      .filter((p): p is NonNullable<typeof p> => p !== null);
    if (policyLinks.length) {
      const pts = policyLinks.flatMap(({ a, b }) => [a.x, a.y, a.z, b.x, b.y, b.z]);
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      kit.scene.add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: themeColor(CATEGORY.policy.var, CATEGORY.policy.fb), transparent: true, opacity: kit.isDark ? 0.28 : 0.35 })));
    }

    // ---------- 连线：二次贝塞尔曲线 + 屏幕空间粗线 ----------
    const resolution = new THREE.Vector2(host.clientWidth, host.clientHeight);
    const curves = links.map(([a, b, edge]) => {
      const p0 = pos[a];
      const p2 = pos[b];
      const mid = p0.clone().add(p2).multiplyScalar(0.5);
      const lift = Math.max(6, p0.distanceTo(p2) * 0.22);
      const p1 = mid.add(new THREE.Vector3(0, lift, 0));
      const curve = new THREE.QuadraticBezierCurve3(p0, p1, p2);
      const pts = curve.getPoints(32).flatMap((p) => [p.x, p.y, p.z]);
      const geom = new LineGeometry();
      geom.setPositions(pts);
      const [name, fb] = EDGE_VAR[edge.kind] ?? ["--text-tertiary", "#8a8d94"];
      const color = themeColor(name, fb);
      const pending = edge.status === "pending";
      const material = new LineMaterial({
        color: color.getHex(), linewidth: pending ? 1.6 : 2.4, transparent: true, opacity: pending ? 0.55 : 0.85,
        dashed: pending, dashSize: 3, gapSize: 3, resolution, worldUnits: false,
      });
      const line = new Line2(geom, material);
      if (pending) line.computeLineDistances();
      kit.scene.add(line);
      return { curve, color, a, b, material };
    });

    // ---------- 方向粒子：沿 source→target 流动，表示关系的方向 ----------
    const perEdge = 2;
    const particleMat = new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false, transparent: true, opacity: 0.95 });
    const particles = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 12, 8), particleMat, Math.max(1, curves.length * perEdge));
    particles.count = curves.length * perEdge;
    const dummy = new THREE.Object3D();
    curves.forEach((c, i) => {
      for (let k = 0; k < perEdge; k++) particles.setColorAt(i * perEdge + k, c.color.clone().multiplyScalar(kit.isDark ? 2.2 : 1.1));
    });
    kit.scene.add(particles);

    // ---------- 交互 ----------
    let hovered = -1;
    focusRef.current = (key) => {
      const i = key ? index.get(key) : undefined;
      if (i !== undefined) kit.focus(pos[i]);
    };
    focusRef.current(selectedRef.current);

    const tmp = new THREE.Vector3();
    kit.onFrame((dt, t) => {
      const k = kit.reducedMotion ? 1 : 1 - Math.pow(0.001, dt);
      const sel = selectedRef.current;
      nodes.forEach((n, i) => {
        const active = n.node.key === sel;
        const hot = i === hovered;
        const neighbour = hovered >= 0 && links.some(([a, b]) => (a === hovered && b === i) || (b === hovered && a === i));
        const targetE = active ? (kit.isDark ? 2.4 : 0.9) : hot ? (kit.isDark ? 1.6 : 0.6) : neighbour ? 0.35 : 0;
        const targetS = n.base * (active ? 1.45 : hot ? 1.25 : 1);
        n.emissive += (targetE - n.emissive) * k;
        n.scale += (targetS - n.scale) * k;
        n.material.emissiveIntensity = n.emissive;
        n.mesh.scale.setScalar(n.scale);
        (n.halo.material as THREE.SpriteMaterial).opacity = Math.min(0.9, n.emissive * (kit.isDark ? 0.35 : 0.5));
        n.el.classList.toggle("is-active", active || hot);
        if (n.spin && !kit.reducedMotion) n.mesh.rotation.y += dt * 0.5;
      });
      policies.forEach((p) => {
        if (!kit.reducedMotion) p.mesh.rotation.y += dt * 0.3;
      });
      // 悬停时，相连的线变亮，其余变淡
      curves.forEach((c) => {
        const touch = hovered >= 0 && (c.a === hovered || c.b === hovered);
        const target = hovered < 0 ? 0.85 : touch ? 1 : 0.18;
        c.material.opacity += (target - c.material.opacity) * k;
        c.material.resolution.set(host.clientWidth, host.clientHeight);
      });
      if (!kit.reducedMotion) {
        curves.forEach((c, i) => {
          for (let p = 0; p < perEdge; p++) {
            const u = (t * 0.22 + p / perEdge + i * 0.137) % 1;
            c.curve.getPoint(u, tmp);
            dummy.position.copy(tmp);
            dummy.scale.setScalar(0.9 + Math.sin(u * Math.PI) * 0.6);
            dummy.updateMatrix();
            particles.setMatrixAt(i * perEdge + p, dummy.matrix);
          }
        });
        particles.instanceMatrix.needsUpdate = true;
      } else {
        particles.visible = false;
      }
      declutterLabels(
        [
          ...nodes.map((n, i) => ({ el: n.el, world: n.label.position, priority: n.node.key === sel ? 3 : i === hovered ? 2 : 0 })),
          ...policies.map((p) => ({ el: p.el, world: p.label.position, priority: 1 })),
        ],
        kit.camera,
        host.clientWidth,
        host.clientHeight,
      );
    });

    const ray = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    const meshes = nodes.map((n) => n.mesh);
    const pick = (e: PointerEvent) => {
      const rect = kit.renderer.domElement.getBoundingClientRect();
      ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
      ray.setFromCamera(ndc, kit.camera);
      const hit = ray.intersectObjects(meshes)[0];
      return hit ? (hit.object.userData.index as number) : -1;
    };
    const canvas = kit.renderer.domElement;
    const onMove = (e: PointerEvent) => {
      hovered = pick(e);
      canvas.style.cursor = hovered >= 0 ? "pointer" : "grab";
      setHover(hovered >= 0 ? { node: nodes[hovered].node, x: e.clientX, y: e.clientY } : null);
    };
    const onLeave = () => {
      hovered = -1;
      setHover(null);
    };
    let downAt = { x: 0, y: 0 };
    const onDown = (e: PointerEvent) => (downAt = { x: e.clientX, y: e.clientY });
    const onUp = (e: PointerEvent) => {
      if (Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) > 4) return; // 拖动旋转不算点击
      const i = pick(e);
      if (i >= 0) selectRef.current(nodes[i].node);
    };
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointerup", onUp);
    return () => {
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointerup", onUp);
      nodes.forEach((n) => n.el.remove());
      policies.forEach((p) => p.el.remove());
      glowTex.dispose();
      focusRef.current = () => {};
      kit.dispose();
    };
    // 主题变了就整体重建：材质、灯光、泛光都按新主题取色。
  }, [data, theme]);

  return (
    <div className="g3d" style={{ height }}>
      <div ref={hostRef} className="g3d-canvas" />
      <ul className="g3d-legend">
        {present.map((c) => (
          <li key={c} className={`g3d-legend-item g3d-legend-item--${c}`}>
            <i aria-hidden="true" />
            {t(`view3d.legend.${c}`)}
          </li>
        ))}
      </ul>
      {hover ? (
        <FloatingTip x={hover.x} y={hover.y}>
          <div className="kn-node-tooltip-code">{hover.node.code}</div>
          <div className="kn-node-tooltip-title">{hover.node.title}</div>
        </FloatingTip>
      ) : null}
    </div>
  );
}
