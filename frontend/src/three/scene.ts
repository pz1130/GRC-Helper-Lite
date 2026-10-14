// 两个 3D 视图共用的场景骨架。画质相关的决定集中在这里：
// - 物理正确的输出：sRGB 输出 + ACES 色调映射，PBR 材质配 RoomEnvironment 的环境反射；
// - 柔和阴影（PCFSoft）、半球光 + 主光；
// - 轻微泛光（UnrealBloom）只对高亮发光的物体起作用：阈值设得高，普通表面不会被抹亮；
// - 相机用 OrbitControls 阻尼，聚焦时对 target 做缓动；
// - 标签走 CSS2DRenderer：文字是真 DOM，清晰且可套主题样式。
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";

import { cssColor, prefersReducedMotion } from "./support";

export interface SceneKit {
  renderer: THREE.WebGLRenderer;
  labelRenderer: CSS2DRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  isDark: boolean;
  reducedMotion: boolean;
  onFrame: (fn: (dt: number, t: number) => void) => void;
  /** 把相机目标缓动到某点（聚焦选中节点用）。 */
  focus: (target: THREE.Vector3) => void;
  dispose: () => void;
}

export function createScene(
  host: HTMLElement,
  opts: {
    autoRotate?: boolean;
    cameraPos: [number, number, number];
    target?: [number, number, number];
    bloom?: number;
    /** 入场镜头：从远处侧上方缓缓推近到 cameraPos。 */
    intro?: boolean;
    /** false 时不接管指针（装饰用小场景）。 */
    interactive?: boolean;
    shadows?: boolean;
  },
): SceneKit {
  const isDark = document.documentElement.getAttribute("data-theme") === "dark";
  const reducedMotion = prefersReducedMotion();
  const width = () => Math.max(1, host.clientWidth);
  const height = () => Math.max(1, host.clientHeight);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance", preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(width(), height());
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  // 环境贴图做了较强模糊（sigma 0.12）：RoomEnvironment 里的灯板若反射得太清晰，
  // 竖直的清漆柱面上会出现一道道水平亮纹（v2 深色主题的「条纹」主要来自这里）。
  // 浅色主题下 ACES 会把高亮色压淡，曝光略降让品牌色更饱满。
  renderer.toneMappingExposure = isDark ? 1.05 : 0.82;
  renderer.shadowMap.enabled = opts.shadows !== false;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.setClearColor(0x000000, 0);
  renderer.domElement.style.display = "block";
  host.appendChild(renderer.domElement);

  const labelRenderer = new CSS2DRenderer();
  labelRenderer.setSize(width(), height());
  labelRenderer.domElement.className = "three-labels";
  host.appendChild(labelRenderer.domElement);

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envTexture = pmrem.fromScene(new RoomEnvironment(), 0.12).texture;
  scene.environment = envTexture;
  scene.environmentIntensity = isDark ? 0.55 : 0.8;

  const camera = new THREE.PerspectiveCamera(40, width() / height(), 0.5, 4000);
  camera.position.set(...opts.cameraPos);

  scene.add(new THREE.HemisphereLight(isDark ? 0xc7d2fe : 0xffffff, isDark ? 0x0b0c10 : 0xdfe3ee, isDark ? 0.7 : 1.1));
  const key = new THREE.DirectionalLight(0xffffff, isDark ? 1.6 : 2.0);
  key.position.set(60, 140, 80);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.radius = 6;
  key.shadow.bias = -0.0002;
  key.shadow.normalBias = 1.2;
  const sc = key.shadow.camera as THREE.OrthographicCamera;
  sc.left = -160; sc.right = 160; sc.top = 160; sc.bottom = -160; sc.near = 10; sc.far = 500;
  scene.add(key);
  const rim = new THREE.DirectionalLight(new THREE.Color(cssColor("--accent-blue", "#5e6ad2")), isDark ? 0.9 : 0.4);
  rim.position.set(-80, 40, -120);
  scene.add(rim);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = reducedMotion ? 0.3 : 0.07;
  controls.rotateSpeed = 0.7;
  controls.zoomSpeed = 0.8;
  controls.autoRotate = !!opts.autoRotate && !reducedMotion;
  controls.autoRotateSpeed = 0.45;
  if (opts.target) controls.target.set(...opts.target);
  controls.enabled = opts.interactive !== false;
  // 用户一动手就停止自转，松手 6 秒后恢复——自转只是「闲置时的展示」。
  let resumeTimer = 0;
  controls.addEventListener("start", () => {
    controls.autoRotate = false;
    window.clearTimeout(resumeTimer);
  });
  controls.addEventListener("end", () => {
    if (!opts.autoRotate || reducedMotion) return;
    resumeTimer = window.setTimeout(() => (controls.autoRotate = true), 6000);
  });

  // 泛光只在深色主题开：后处理链路里 OutputPass 会对整幅画面做色调映射，
  // 浅色主题的白底会被压灰；浅色下直接渲染，背景保持透明、与卡片同色。
  const useBloom = isDark && opts.bloom !== 0;
  if (useBloom) scene.background = themeColor("--stage-card", "#16171a");
  const composer = new EffectComposer(renderer);
  composer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  composer.setSize(width(), height());
  composer.addPass(new RenderPass(scene, camera));
  const bloom = new UnrealBloomPass(new THREE.Vector2(width(), height()), opts.bloom ?? 0.7, 0.5, 0.82);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  const frameFns: ((dt: number, t: number) => void)[] = [];
  const clock = new THREE.Clock();
  const focusTarget = new THREE.Vector3();
  let focusing = false;
  // 入场镜头：起点在终点外侧、更高、绕 Y 轴偏一点，用 easeOutCubic 推近。
  const introEnd = camera.position.clone();
  const introFrom = introEnd.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), -0.7).multiplyScalar(1.55).setY(introEnd.y * 2.1);
  const INTRO = 1.8;
  let introT = opts.intro && !reducedMotion ? 0 : INTRO;
  if (introT < INTRO) camera.position.copy(introFrom);
  let raf = 0;
  let visible = true;
  const loop = () => {
    raf = requestAnimationFrame(loop);
    if (!visible || document.hidden) return;
    const dt = Math.min(clock.getDelta(), 0.05);
    const t = clock.elapsedTime;
    for (const fn of frameFns) fn(dt, t);
    if (introT < INTRO) {
      introT = Math.min(INTRO, introT + dt);
      const e = 1 - Math.pow(1 - introT / INTRO, 3);
      camera.position.lerpVectors(introFrom, introEnd, e);
    }
    if (focusing) {
      controls.target.lerp(focusTarget, reducedMotion ? 1 : 1 - Math.pow(0.002, dt));
      if (controls.target.distanceTo(focusTarget) < 0.05) focusing = false;
    }
    controls.update();
    if (useBloom) composer.render();
    else renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
  };
  loop();

  const io = new IntersectionObserver(([entry]) => (visible = entry.isIntersecting));
  io.observe(host);
  const ro = new ResizeObserver(() => {
    renderer.setSize(width(), height());
    composer.setSize(width(), height());
    labelRenderer.setSize(width(), height());
    camera.aspect = width() / height();
    camera.updateProjectionMatrix();
  });
  ro.observe(host);

  return {
    renderer, labelRenderer, scene, camera, controls, isDark, reducedMotion,
    onFrame: (fn) => frameFns.push(fn),
    focus: (target) => {
      focusTarget.copy(target);
      focusing = true;
    },
    dispose: () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(resumeTimer);
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
      envTexture.dispose();
      pmrem.dispose();
      composer.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      labelRenderer.domElement.remove();
    },
  };
}

export function themeColor(name: string, fallback: string): THREE.Color {
  return new THREE.Color(cssColor(name, fallback));
}

/**
 * 带径向淡出的网格地面：线条在中心清晰、向外渐隐，不出现一块生硬的方形边界。
 * 同时铺一层只接收阴影的平面，让物体「落」在地上。
 */
export function createFloor(size: number, cell: number, isDark: boolean): THREE.Group {
  const group = new THREE.Group();
  const lineColor = themeColor("--stage-border-hover", isDark ? "#2d3b52" : "#c9d0db");
  const grid = new THREE.Mesh(
    new THREE.PlaneGeometry(size, size),
    new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      uniforms: {
        uColor: { value: lineColor },
        uCell: { value: cell },
        uFade: { value: size * 0.5 },
        uOpacity: { value: isDark ? 0.55 : 0.75 },
      },
      vertexShader: `
        varying vec2 vPos;
        void main() {
          vPos = position.xy;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }`,
      fragmentShader: `
        uniform vec3 uColor; uniform float uCell; uniform float uFade; uniform float uOpacity;
        varying vec2 vPos;
        void main() {
          vec2 g = abs(fract(vPos / uCell - 0.5) - 0.5) / fwidth(vPos / uCell);
          float line = 1.0 - min(min(g.x, g.y), 1.0);
          float fade = 1.0 - smoothstep(uFade * 0.35, uFade, length(vPos));
          gl_FragColor = vec4(uColor, line * fade * uOpacity);
          #include <colorspace_fragment>
        }`,
    }),
  );
  grid.rotation.x = -Math.PI / 2;
  group.add(grid);
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(size, size), new THREE.ShadowMaterial({ opacity: isDark ? 0.45 : 0.14 }));
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = -0.05;
  shadow.receiveShadow = true;
  group.add(shadow);
  return group;
}

/**
 * 标签防重叠：按离相机由近到远排，逐个检查屏幕矩形，与已放置的相交就隐藏。
 * 近的、被选中的优先显示；每帧做一次，几十个标签开销可忽略。
 */
export function declutterLabels(
  items: { el: HTMLElement; world: THREE.Vector3; priority: number }[],
  camera: THREE.Camera,
  width: number,
  height: number,
) {
  const v = new THREE.Vector3();
  const placed: { x: number; y: number; w: number; h: number }[] = [];
  const ranked = items
    .map((item) => {
      v.copy(item.world).project(camera);
      return { item, x: ((v.x + 1) / 2) * width, y: ((1 - v.y) / 2) * height, z: v.z };
    })
    .sort((a, b) => b.item.priority - a.item.priority || a.z - b.z);
  for (const r of ranked) {
    const w = r.item.el.offsetWidth || 40;
    const h = r.item.el.offsetHeight || 18;
    const rect = { x: r.x - w / 2, y: r.y - h, w, h };
    const off = r.z > 1 || rect.x < 0 || rect.y < 0 || rect.x + w > width || rect.y + h > height;
    const hit = off || placed.some((p) => rect.x < p.x + p.w + 4 && rect.x + rect.w + 4 > p.x && rect.y < p.y + p.h + 2 && rect.y + rect.h + 2 > p.y);
    r.item.el.classList.toggle("is-hidden", hit);
    if (!hit) placed.push(rect);
  }
}

let contactTex: THREE.Texture | null = null;
/**
 * 「接触阴影」：贴在地面上的径向渐变圆斑。比 SSAO 便宜得多（SwiftShader 下也流畅），
 * 又能让物体真正「贴地」——实时阴影负责方向感，接触阴影负责落地感。
 */
export function contactShadow(radius: number, isDark: boolean): THREE.Mesh {
  if (!contactTex) {
    const c = document.createElement("canvas");
    c.width = c.height = 128;
    const g = c.getContext("2d")!;
    const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    grad.addColorStop(0, "rgba(0,0,0,0.85)");
    grad.addColorStop(0.45, "rgba(0,0,0,0.35)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 128, 128);
    contactTex = new THREE.CanvasTexture(c);
  }
  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(radius * 2, radius * 2),
    new THREE.MeshBasicMaterial({ map: contactTex, transparent: true, depthWrite: false, opacity: isDark ? 0.75 : 0.32, toneMapped: false }),
  );
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.y = 0.06;
  mesh.renderOrder = -1;
  return mesh;
}

/** 浅色主题里 ACES 会把颜色压粉，按 HSL 补一点饱和、压一点亮度，让色块更「实」。 */
export function richen(color: THREE.Color, isDark: boolean): THREE.Color {
  return isDark ? color : color.clone().offsetHSL(0, 0.12, -0.07);
}
