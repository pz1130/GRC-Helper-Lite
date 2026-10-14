// 两个 3D 视图共用的场景骨架：渲染器、相机、轨道控制、主题光照、尺寸跟随与释放。
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import { cssColor, prefersReducedMotion } from "./support";

export interface SceneKit {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  isDark: boolean;
  /** 每帧回调；返回 false 表示这一帧不需要重画。 */
  onFrame: (fn: (dt: number) => void) => void;
  dispose: () => void;
}

export function createScene(host: HTMLElement, opts: { autoRotate?: boolean; cameraPos: [number, number, number] }): SceneKit {
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power", preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(host.clientWidth, host.clientHeight);
  renderer.domElement.style.display = "block";
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, host.clientWidth / Math.max(1, host.clientHeight), 0.1, 2000);
  camera.position.set(...opts.cameraPos);

  scene.add(new THREE.HemisphereLight(0xffffff, isDark ? 0x202030 : 0xd8dbe8, isDark ? 1.1 : 1.6));
  const key = new THREE.DirectionalLight(0xffffff, isDark ? 1.4 : 1.2);
  key.position.set(40, 80, 60);
  scene.add(key);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  // 减少动态：不自转，阻尼也关掉，转动只跟手。
  const reduced = prefersReducedMotion();
  controls.autoRotate = !!opts.autoRotate && !reduced;
  controls.autoRotateSpeed = 0.6;
  if (reduced) controls.enableDamping = false;

  const frameFns: ((dt: number) => void)[] = [];
  const clock = new THREE.Clock();
  let raf = 0;
  let visible = true;
  const loop = () => {
    raf = requestAnimationFrame(loop);
    if (!visible) return;
    const dt = Math.min(clock.getDelta(), 0.05);
    for (const fn of frameFns) fn(dt);
    controls.update();
    renderer.render(scene, camera);
  };
  loop();

  // 画布滚出视口就停画，省电。
  const io = new IntersectionObserver(([entry]) => (visible = entry.isIntersecting));
  io.observe(host);
  const ro = new ResizeObserver(() => {
    const w = host.clientWidth;
    const h = host.clientHeight;
    renderer.setSize(w, h);
    camera.aspect = w / Math.max(1, h);
    camera.updateProjectionMatrix();
  });
  ro.observe(host);

  return {
    renderer, scene, camera, controls, isDark,
    onFrame: (fn) => frameFns.push(fn),
    dispose: () => {
      cancelAnimationFrame(raf);
      io.disconnect();
      ro.disconnect();
      controls.dispose();
      scene.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        mesh.geometry?.dispose();
        const mat = mesh.material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
        else mat?.dispose();
      });
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}

export function themeColor(name: string, fallback: string): THREE.Color {
  return new THREE.Color(cssColor(name, fallback));
}
