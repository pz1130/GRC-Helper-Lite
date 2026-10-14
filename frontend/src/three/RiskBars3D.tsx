// 风险热力图的 3D 柱状视图（按需加载的分包）：x = 可能性，z = 影响，
// 柱高 = 未关闭风险数，柱色按分数档位，与 2D 矩阵同一套高/中/低语义。
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";

import { useTheme } from "../theme";
import { FloatingTip } from "./FloatingTip";
import { createFloor, createScene, themeColor } from "./scene";

export interface RiskCell { likelihood: number; impact: number; count: number }

const CELL = 12;

function tone(score: number): [string, string] {
  if (score >= 15) return ["--accent-ruby", "#e11d48"];
  if (score >= 8) return ["--accent-amber", "#d97706"];
  return ["--accent-emerald", "#059669"];
}

function label(text: string, className: string, at: THREE.Vector3): CSS2DObject {
  const el = document.createElement("div");
  el.className = className;
  el.textContent = text;
  const obj = new CSS2DObject(el);
  obj.position.copy(at);
  return obj;
}

export default function RiskBars3D({ cells }: { cells: RiskCell[] }) {
  const { t } = useTranslation();
  const { theme } = useTheme();
  const hostRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ cell: RiskCell; x: number; y: number } | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const kit = createScene(host, { cameraPos: [62, 70, 88], target: [0, 8, 0], bloom: 0.35 });
    kit.controls.maxPolarAngle = Math.PI / 2.15;
    kit.controls.minDistance = 70;
    kit.controls.maxDistance = 220;

    const offset = (n: number) => (n - 3) * CELL;
    const max = Math.max(1, ...cells.map((c) => c.count));
    kit.scene.add(createFloor(260, CELL, kit.isDark));

    const tileGeo = new RoundedBoxGeometry(CELL - 1.4, 0.8, CELL - 1.4, 3, 0.4);
    const bars: { mesh: THREE.Mesh; material: THREE.MeshPhysicalMaterial; cell: RiskCell; height: number; grow: number; glow: number; tag: CSS2DObject }[] = [];

    for (const cell of cells) {
      const [name, fb] = tone(cell.likelihood * cell.impact);
      const color = themeColor(name, fb);
      const tile = new THREE.Mesh(
        tileGeo,
        new THREE.MeshPhysicalMaterial({ color, roughness: 0.6, metalness: 0, transparent: true, opacity: kit.isDark ? 0.28 : 0.22, clearcoat: 0.3 }),
      );
      tile.position.set(offset(cell.likelihood), 0.4, -offset(cell.impact));
      tile.receiveShadow = true;
      kit.scene.add(tile);
      if (cell.count === 0) continue;
      const material = new THREE.MeshPhysicalMaterial({
        color, roughness: 0.18, metalness: 0.05, clearcoat: 1, clearcoatRoughness: 0.08,
        emissive: color, emissiveIntensity: 0, envMapIntensity: kit.isDark ? 0.6 : 1,
      });
      // 每根柱子按最终高度单独建几何体：拉伸一个单位高的圆角盒会把圆角和法线一起拉坏，
      // 表面出现条纹。升起动画只在 0→1 之间缩放，最终状态没有形变。
      const height = 6 + (cell.count / max) * 30;
      const barGeo = new RoundedBoxGeometry(CELL - 4, height, CELL - 4, 4, 0.6);
      barGeo.translate(0, height / 2, 0);
      const mesh = new THREE.Mesh(barGeo, material);
      mesh.position.set(offset(cell.likelihood), 0.8, -offset(cell.impact));
      mesh.castShadow = true;
      // 柱子只投影不接收阴影：竖直侧面自阴影在 SwiftShader 上会产生条纹（shadow acne）。
      mesh.receiveShadow = false;
      mesh.scale.y = 0.01;
      const tag = label(String(cell.count), "r3d-count", new THREE.Vector3(mesh.position.x, 0, mesh.position.z));
      kit.scene.add(mesh, tag);
      bars.push({ mesh, material, cell, height, grow: kit.reducedMotion ? 1 : 0, glow: 0, tag });
    }

    // 坐标轴：刻度数字 + 轴名，全部 HTML，清晰且随主题配色
    for (let n = 1; n <= 5; n++) {
      kit.scene.add(label(String(n), "r3d-tick", new THREE.Vector3(offset(n), 0.5, CELL * 3.1)));
      kit.scene.add(label(String(n), "r3d-tick", new THREE.Vector3(-CELL * 3.1, 0.5, -offset(n))));
    }
    kit.scene.add(label(`${t("risks.likelihood")} →`, "r3d-axis", new THREE.Vector3(0, 0.5, CELL * 3.9)));
    kit.scene.add(label(`${t("risks.impact")} →`, "r3d-axis", new THREE.Vector3(-CELL * 4, 0.5, 0)));

    let hovered: (typeof bars)[number] | null = null;
    kit.onFrame((dt) => {
      const k = kit.reducedMotion ? 1 : 1 - Math.pow(0.002, dt);
      bars.forEach((bar, i) => {
        // 依次升起：每根柱子错开一点，像数据「长」出来
        bar.grow = Math.min(1, bar.grow + dt * 1.6 - (bar.grow === 0 ? i * 0.004 : 0));
        const ease = 1 - Math.pow(1 - Math.max(0, bar.grow), 3);
        const lift = hovered === bar ? 1.06 : 1;
        bar.mesh.scale.y = Math.max(0.01, ease * lift);
        const targetGlow = hovered === bar ? (kit.isDark ? 1.4 : 0.45) : 0;
        bar.glow += (targetGlow - bar.glow) * k;
        bar.material.emissiveIntensity = bar.glow;
        const dim = hovered && hovered !== bar ? 0.55 : 1;
        bar.material.opacity = dim;
        bar.material.transparent = dim < 1;
        bar.tag.position.y = bar.mesh.position.y + bar.height * bar.mesh.scale.y + 2;
      });
    });

    const ray = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    const canvas = kit.renderer.domElement;
    const onMove = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
      ray.setFromCamera(ndc, kit.camera);
      const hit = ray.intersectObjects(bars.map((b) => b.mesh))[0];
      hovered = hit ? bars.find((b) => b.mesh === hit.object) ?? null : null;
      canvas.style.cursor = hovered ? "pointer" : "grab";
      setHover(hovered ? { cell: hovered.cell, x: e.clientX, y: e.clientY } : null);
    };
    const onLeave = () => {
      hovered = null;
      setHover(null);
    };
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerleave", onLeave);
    return () => {
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerleave", onLeave);
      kit.dispose();
    };
  }, [cells, t, theme]);

  return (
    <div className="r3d">
      <div ref={hostRef} className="r3d-canvas" />
      {hover ? (
        <FloatingTip x={hover.x} y={hover.y}>
          <div className="kn-node-tooltip-title">
            {t("risks.matrixCell", { impact: hover.cell.impact, likelihood: hover.cell.likelihood, count: hover.cell.count })}
          </div>
          <div className="kn-node-tooltip-meta">{t("view3d.score", { value: hover.cell.impact * hover.cell.likelihood })}</div>
        </FloatingTip>
      ) : null}
    </div>
  );
}
