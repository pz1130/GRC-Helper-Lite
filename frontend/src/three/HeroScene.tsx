// 总览页 hero 的装饰 3D：三层半透明「治理平面」（框架 / 制度 / 控制），
// 层间有发光的连接点缓缓流动，随指针轻微视差。纯装饰：aria-hidden，不接管指针拖拽。
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";

import { useTheme } from "../theme";
import { createScene, richen, themeColor } from "./scene";

const LAYERS: [string, string][] = [
  ["--accent-blue", "#5e6ad2"],
  ["--accent-emerald", "#059669"],
  ["--accent-amber", "#d97706"],
];

export default function HeroScene() {
  const { theme } = useTheme();
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    // 透明背景（bloom: 0）才能和 hero 的底色融为一体；装饰小图不需要实时阴影。
    const kit = createScene(host, { cameraPos: [30, 24, 36], target: [0, 8, 0], bloom: 0, interactive: false, shadows: false, intro: true });
    const slabGeo = new RoundedBoxGeometry(30, 1.6, 30, 4, 0.8);
    const slabs = LAYERS.map(([v, fb], i) => {
      const color = richen(themeColor(v, fb), kit.isDark);
      const mat = new THREE.MeshPhysicalMaterial({
        color, roughness: 0.15, metalness: 0, transmission: 0.55, thickness: 3, ior: 1.35,
        clearcoat: 1, clearcoatRoughness: 0.15, transparent: true, opacity: 0.92,
        emissive: color, emissiveIntensity: kit.isDark ? 0.18 : 0.04, envMapIntensity: 1,
      });
      const mesh = new THREE.Mesh(slabGeo, mat);
      mesh.position.y = i * 9;
      // 每层的边框描一圈亮线，让玻璃板的轮廓在浅色底上也清楚
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(slabGeo, 30), new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.7 }));
      mesh.add(edges);
      kit.scene.add(mesh);
      return mesh;
    });
    // 层间的「映射关系」：几根竖直细线 + 沿线流动的亮点
    const dotMat = new THREE.MeshBasicMaterial({ color: kit.isDark ? 0xffffff : 0x1f2937, toneMapped: false });
    const anchors = [[-8, -6], [6, -9], [9, 7], [-7, 8], [0, 0]];
    const lineMat = new THREE.LineBasicMaterial({ color: themeColor("--text-tertiary", "#8a8d94"), transparent: true, opacity: 0.45 });
    const dots = anchors.map(([x, z], i) => {
      const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(x, 0, z), new THREE.Vector3(x, 18, z)]);
      kit.scene.add(new THREE.Line(g, lineMat));
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.7, 16, 12), dotMat);
      dot.position.set(x, 0, z);
      kit.scene.add(dot);
      return { dot, phase: i * 0.21 };
    });

    let px = 0;
    let py = 0;
    const onPointer = (e: PointerEvent) => {
      px = (e.clientX / window.innerWidth - 0.5) * 2;
      py = (e.clientY / window.innerHeight - 0.5) * 2;
    };
    window.addEventListener("pointermove", onPointer, { passive: true });
    kit.onFrame((_dt, t) => {
      const motion = kit.reducedMotion ? 0 : 1;
      slabs.forEach((s, i) => {
        s.position.y = i * 9 + Math.sin(t * 0.8 + i * 1.3) * 0.7 * motion;
        s.rotation.y = -0.18 + px * 0.12 * motion;
        s.rotation.x = py * 0.05 * motion;
      });
      dots.forEach((d) => (d.dot.position.y = ((t * 0.25 + d.phase) % 1) * 18 * motion + (1 - motion) * 9));
    });
    return () => {
      window.removeEventListener("pointermove", onPointer);
      kit.dispose();
    };
  }, [theme]);

  return <div ref={hostRef} className="kn-hero-3d" aria-hidden="true" />;
}
