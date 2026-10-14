// 风险热力图的 3D 柱状视图（按需加载的分包）：x = 可能性，z = 影响，
// 柱高 = 未关闭风险数，柱色按分数档位，与 2D 矩阵同一套高/中/低语义。
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import * as THREE from "three";

import { createScene, themeColor } from "./scene";

export interface RiskCell { likelihood: number; impact: number; count: number }

const CELL = 12;

function tone(score: number): [string, string] {
  if (score >= 15) return ["--accent-ruby", "#e11d48"];
  if (score >= 8) return ["--accent-amber", "#d97706"];
  return ["--accent-emerald", "#059669"];
}

export default function RiskBars3D({ cells }: { cells: RiskCell[] }) {
  const { t } = useTranslation();
  const hostRef = useRef<HTMLDivElement>(null);
  const axisRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ cell: RiskCell; x: number; y: number } | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    const axisHost = axisRef.current;
    if (!host || !axisHost) return;
    const kit = createScene(host, { cameraPos: [70, 78, 92] });
    kit.controls.target.set(0, 6, 0);
    kit.controls.maxPolarAngle = Math.PI / 2.1;
    kit.controls.minDistance = 60;
    kit.controls.maxDistance = 220;

    const max = Math.max(1, ...cells.map((c) => c.count));
    const offset = (n: number) => (n - 3) * CELL;
    const base = new THREE.Group();
    const tileGeo = new THREE.BoxGeometry(CELL - 1.2, 0.6, CELL - 1.2);
    const barGeo = new THREE.BoxGeometry(CELL - 3.5, 1, CELL - 3.5);
    barGeo.translate(0, 0.5, 0);
    const bars: THREE.Mesh[] = [];
    for (const cell of cells) {
      const [name, fb] = tone(cell.likelihood * cell.impact);
      const color = themeColor(name, fb);
      const tile = new THREE.Mesh(tileGeo, new THREE.MeshStandardMaterial({ color, transparent: true, opacity: kit.isDark ? 0.22 : 0.18, roughness: 0.9 }));
      tile.position.set(offset(cell.likelihood), 0, -offset(cell.impact));
      base.add(tile);
      if (cell.count > 0) {
        const bar = new THREE.Mesh(barGeo, new THREE.MeshStandardMaterial({ color, roughness: 0.4, metalness: 0.05 }));
        bar.position.set(offset(cell.likelihood), 0.3, -offset(cell.impact));
        bar.userData = { cell, target: 6 + (cell.count / max) * 34 };
        bar.scale.y = 0.01;
        bars.push(bar);
        base.add(bar);
      }
    }
    kit.scene.add(base);
    const grid = new THREE.GridHelper(CELL * 5, 5, themeColor("--stage-border-hover", "#c9d0db"), themeColor("--stage-border", "#e3e7ee"));
    grid.position.y = -0.31;
    kit.scene.add(grid);

    // 坐标轴刻度用 HTML 叠层：文字清晰、跟随主题字体。
    const ticks: { el: HTMLSpanElement; pos: THREE.Vector3 }[] = [];
    const addTick = (text: string, pos: THREE.Vector3, cls = "") => {
      const el = document.createElement("span");
      el.className = `r3d-tick ${cls}`;
      el.textContent = text;
      axisHost.appendChild(el);
      ticks.push({ el, pos });
    };
    for (let n = 1; n <= 5; n++) {
      addTick(String(n), new THREE.Vector3(offset(n), 0, CELL * 3.2));
      addTick(String(n), new THREE.Vector3(-CELL * 3.2, 0, -offset(n)));
    }
    addTick(t("risks.likelihood"), new THREE.Vector3(0, 0, CELL * 4.1), "r3d-axis");
    addTick(t("risks.impact"), new THREE.Vector3(-CELL * 4.2, 0, 0), "r3d-axis");

    const v = new THREE.Vector3();
    kit.onFrame(() => {
      // 柱子从地面升起；减少动态时 scene.ts 不会自转，这里也直接到位。
      const instant = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      for (const bar of bars) {
        const target = bar.userData.target as number;
        bar.scale.y = instant ? target : bar.scale.y + (target - bar.scale.y) * 0.12;
      }
      const w = host.clientWidth;
      const h = host.clientHeight;
      for (const tick of ticks) {
        v.copy(tick.pos).project(kit.camera);
        tick.el.style.transform = `translate(-50%, -50%) translate(${((v.x + 1) / 2) * w}px, ${((1 - v.y) / 2) * h}px)`;
      }
    });

    const ray = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    const onMove = (e: PointerEvent) => {
      const rect = kit.renderer.domElement.getBoundingClientRect();
      ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
      ray.setFromCamera(ndc, kit.camera);
      const hit = ray.intersectObjects(bars)[0];
      setHover(hit ? { cell: hit.object.userData.cell as RiskCell, x: e.clientX - rect.left, y: e.clientY - rect.top } : null);
    };
    kit.renderer.domElement.addEventListener("pointermove", onMove);
    return () => {
      kit.renderer.domElement.removeEventListener("pointermove", onMove);
      ticks.forEach((tick) => tick.el.remove());
      tileGeo.dispose();
      barGeo.dispose();
      kit.dispose();
    };
  }, [cells, t]);

  return (
    <div className="r3d">
      <div ref={hostRef} className="r3d-canvas" />
      <div ref={axisRef} className="r3d-ticks" aria-hidden="true" />
      {hover ? (
        <div className="kn-node-tooltip g3d-tooltip" style={{ left: hover.x, top: hover.y }}>
          {t("risks.matrixCell", { impact: hover.cell.impact, likelihood: hover.cell.likelihood, count: hover.cell.count })}
        </div>
      ) : null}
    </div>
  );
}
