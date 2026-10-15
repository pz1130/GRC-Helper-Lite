// 总览 hero v2：三个可切换的概念（?hero=a|b|c 对比用，默认见 heroConcept()）。
// 设计取舍：
// - 不走 scene.ts 的 PBR + 后处理管线：hero 是纯装饰，用自写 shader + 叠加混合就能得到「发光」，
//   省掉 EffectComposer 的整屏多次绘制，低端集显也能稳 60fps；
// - antialias + 设备像素比（上限 2）保证 125%/150% 缩放下线条不发虚；
// - 屏幕外或标签页隐藏时停帧；动画档位 off 只画一帧静态图，reduced 时速度降到 30% 且不跟随指针；
// - 浅色主题用正常混合 + 更深的颜色（叠加混合在白底上会「消失」）。
import { useEffect, useRef } from "react";
import * as THREE from "three";

import { motionLevel } from "../motion";
import { useTheme } from "../theme";
import { cssColor } from "./support";

export type HeroConcept = "a" | "b" | "c";
export interface RiskPoint { likelihood: number; impact: number; score: number; open: boolean }

interface Built { update: (t: number, px: number, py: number) => void; dispose: () => void }

export default function HeroV2({ concept, risks }: { concept: HeroConcept; risks?: RiskPoint[] }) {
  const { theme } = useTheme();
  const hostRef = useRef<HTMLDivElement>(null);
  const riskKey = JSON.stringify(risks ?? []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const isDark = theme === "dark";
    const level = motionLevel();
    const speed = level === "full" ? 1 : level === "reduced" ? 0.3 : 0;
    const follow = level === "full";

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance", preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.appendChild(renderer.domElement);
    renderer.domElement.className = "hero-v2-canvas";

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 200);
    const palette = {
      a: new THREE.Color(cssColor("--accent-blue", "#5e6ad2")),
      b: new THREE.Color(cssColor("--accent-emerald", "#059669")),
      c: new THREE.Color(cssColor("--accent-amber", "#d97706")),
      d: new THREE.Color(cssColor("--accent-ruby", "#e11d48")),
    };
    const ctx = { scene, camera, isDark, palette };
    const built: Built =
      concept === "b" ? buildAurora(ctx) : concept === "c" ? buildTerrain(ctx, JSON.parse(riskKey) as RiskPoint[]) : buildGlobe(ctx);

    const resize = () => {
      const w = Math.max(1, host.clientWidth);
      const h = Math.max(1, host.clientHeight);
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(host);

    // 指针：在整个窗口里取相对位置，缓动跟随
    let tx = 0, ty = 0, px = 0, py = 0;
    const onPointer = (e: PointerEvent) => {
      tx = (e.clientX / window.innerWidth - 0.5) * 2;
      ty = (e.clientY / window.innerHeight - 0.5) * 2;
    };
    if (follow) window.addEventListener("pointermove", onPointer, { passive: true });

    let visible = true;
    const io = new IntersectionObserver(([entry]) => (visible = entry.isIntersecting));
    io.observe(host);

    let raf = 0;
    let simT = 4; // 从第 4 秒开始：静态帧也是一个「已展开」的好看构图
    let last = performance.now();
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      if (!visible || document.hidden) return;
      simT += dt * speed;
      px += (tx - px) * 0.04;
      py += (ty - py) * 0.04;
      built.update(simT, px, py);
      renderer.render(scene, camera);
    };
    if (speed === 0) {
      built.update(simT, 0, 0);
      renderer.render(scene, camera);
    } else raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      window.removeEventListener("pointermove", onPointer);
      built.dispose();
      scene.traverse((o) => {
        const m = o as THREE.Mesh;
        m.geometry?.dispose();
        const mat = m.material as THREE.Material | THREE.Material[] | undefined;
        (Array.isArray(mat) ? mat : mat ? [mat] : []).forEach((x) => x.dispose());
      });
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [theme, concept, riskKey]);

  return <div ref={hostRef} className={`hero-v2 hero-v2--${concept}`} aria-hidden="true" />;
}

type Ctx = { scene: THREE.Scene; camera: THREE.PerspectiveCamera; isDark: boolean; palette: Record<"a" | "b" | "c" | "d", THREE.Color> };

/* ------------------------------------------------------------------ *
 * (a) 控制网络地球：玻璃球壳 + 经纬细线 + 球面上的控制节点，
 *     节点之间的弧线上有光点流动（「映射关系」在框架之间传递）。
 * ------------------------------------------------------------------ */
function buildGlobe({ scene, camera, isDark, palette }: Ctx): Built {
  camera.position.set(0, 0.4, 11.5);
  camera.lookAt(0, 0, 0);
  const root = new THREE.Group();
  root.rotation.z = 0.32;
  scene.add(root);
  const R = 2.6;
  const blend = isDark ? THREE.AdditiveBlending : THREE.NormalBlending;

  // 菲涅尔玻璃壳：边缘亮、中心透
  const shell = new THREE.Mesh(
    new THREE.SphereGeometry(R, 96, 64),
    new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: blend,
      uniforms: { uA: { value: palette.a }, uB: { value: palette.b }, uK: { value: isDark ? 0.9 : 0.55 } },
      vertexShader: `varying vec3 vN; varying vec3 vV; varying float vY;
        void main(){ vec4 mv = modelViewMatrix*vec4(position,1.); vN = normalize(normalMatrix*normal); vV = normalize(-mv.xyz); vY = position.y; gl_Position = projectionMatrix*mv; }`,
      fragmentShader: `uniform vec3 uA; uniform vec3 uB; uniform float uK; varying vec3 vN; varying vec3 vV; varying float vY;
        void main(){ float f = pow(1.-abs(dot(vN,vV)), 2.6); vec3 c = mix(uA,uB, smoothstep(-2.6,2.6,vY)); gl_FragColor = vec4(c, f*uK); }`,
    }),
  );
  root.add(shell);

  // 经纬线
  const gridMat = new THREE.LineBasicMaterial({ color: palette.a, transparent: true, opacity: isDark ? 0.16 : 0.22, blending: blend, depthWrite: false });
  for (let i = 1; i < 9; i++) {
    const lat = -Math.PI / 2 + (i * Math.PI) / 9;
    const pts = [];
    for (let k = 0; k <= 128; k++) { const a = (k / 128) * Math.PI * 2; pts.push(new THREE.Vector3(Math.cos(lat) * Math.cos(a) * R, Math.sin(lat) * R, Math.cos(lat) * Math.sin(a) * R)); }
    root.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), gridMat));
  }
  for (let i = 0; i < 12; i++) {
    const lon = (i / 12) * Math.PI * 2;
    const pts = [];
    for (let k = 0; k <= 96; k++) { const la = -Math.PI / 2 + (k / 96) * Math.PI; pts.push(new THREE.Vector3(Math.cos(la) * Math.cos(lon) * R, Math.sin(la) * R, Math.cos(la) * Math.sin(lon) * R)); }
    root.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), gridMat));
  }

  // 节点：斐波那契球面分布，三种颜色代表框架 / 制度 / 控制
  const N = 46;
  const nodes: THREE.Vector3[] = [];
  const nodePos = new Float32Array(N * 3), nodeCol = new Float32Array(N * 3), nodeSize = new Float32Array(N);
  const cols = [palette.a, palette.b, palette.c];
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963;
    const v = new THREE.Vector3(Math.cos(th) * r, y, Math.sin(th) * r).multiplyScalar(R * 1.005);
    nodes.push(v);
    v.toArray(nodePos, i * 3);
    cols[i % 3].toArray(nodeCol, i * 3);
    nodeSize[i] = i % 7 === 0 ? 1.8 : 1;
  }
  const dotMat = glowPoints(isDark, 110);
  const nodeGeo = new THREE.BufferGeometry();
  nodeGeo.setAttribute("position", new THREE.BufferAttribute(nodePos, 3));
  nodeGeo.setAttribute("color", new THREE.BufferAttribute(nodeCol, 3));
  nodeGeo.setAttribute("size", new THREE.BufferAttribute(nodeSize, 1));
  root.add(new THREE.Points(nodeGeo, dotMat));

  // 弧线 + 沿线流光（shader 里按弧长参数 t 画一个移动的亮段）
  const arcMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: blend,
    uniforms: { uT: { value: 0 }, uBase: { value: isDark ? 0.18 : 0.25 } },
    vertexShader: `attribute float t; attribute float seed; attribute vec3 color; varying float vT; varying float vS; varying vec3 vC;
      void main(){ vT=t; vS=seed; vC=color; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
    fragmentShader: `uniform float uT; uniform float uBase; varying float vT; varying float vS; varying vec3 vC;
      void main(){ float head = fract(uT*0.22 + vS); float d = vT - head; float trail = d < 0. ? exp(d*14.) : exp(-d*60.);
        gl_FragColor = vec4(vC, uBase + trail*0.95); }`,
  });
  const arcs: number[] = [], ts: number[] = [], seeds: number[] = [], acol: number[] = [];
  const pairs: [number, number][] = [];
  for (let i = 0; i < N; i++) {
    let best = -1, bd = 1e9;
    for (let j = 0; j < N; j++) { if (j === i) continue; const d = nodes[i].distanceTo(nodes[j]); if (d > 1.2 && d < bd && (i * 7 + j) % 3 === 0) { bd = d; best = j; } }
    if (best >= 0 && i % 2 === 0) pairs.push([i, best]);
  }
  pairs.forEach(([i, j], k) => {
    const a = nodes[i], b = nodes[j];
    const mid = a.clone().add(b).multiplyScalar(0.5);
    mid.setLength(R + a.distanceTo(b) * 0.38);
    const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
    const pts = curve.getPoints(48);
    const c = cols[k % 3], s = (k * 0.137) % 1;
    for (let p = 0; p < pts.length - 1; p++) {
      arcs.push(...pts[p].toArray(), ...pts[p + 1].toArray());
      ts.push(p / 48, (p + 1) / 48);
      seeds.push(s, s);
      acol.push(c.r, c.g, c.b, c.r, c.g, c.b);
    }
  });
  const arcGeo = new THREE.BufferGeometry();
  arcGeo.setAttribute("position", new THREE.Float32BufferAttribute(arcs, 3));
  arcGeo.setAttribute("t", new THREE.Float32BufferAttribute(ts, 1));
  arcGeo.setAttribute("seed", new THREE.Float32BufferAttribute(seeds, 1));
  arcGeo.setAttribute("color", new THREE.Float32BufferAttribute(acol, 3));
  root.add(new THREE.LineSegments(arcGeo, arcMat));

  // 外圈轨道环
  const ring = new THREE.Mesh(new THREE.TorusGeometry(R * 1.32, 0.008, 6, 200), new THREE.MeshBasicMaterial({ color: palette.b, transparent: true, opacity: isDark ? 0.35 : 0.4, blending: blend, depthWrite: false }));
  ring.rotation.x = Math.PI / 2.25;
  scene.add(ring);

  return {
    update(t, px, py) {
      root.rotation.y = t * 0.12 + px * 0.25;
      root.rotation.x = py * 0.12;
      ring.rotation.z = t * 0.05;
      arcMat.uniforms.uT.value = t;
      dotMat.uniforms.uT.value = t;
    },
    dispose() {},
  };
}

/** 柔光圆点（软边 + 轻微呼吸），三个概念共用。 */
function glowPoints(isDark: boolean, px: number) {
  return new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: isDark ? THREE.AdditiveBlending : THREE.NormalBlending,
    uniforms: { uT: { value: 0 }, uPx: { value: px * Math.min(window.devicePixelRatio || 1, 2) }, uDark: { value: isDark ? 1 : 0 } },
    vertexShader: `attribute float size; attribute vec3 color; uniform float uT; uniform float uPx; varying vec3 vC; varying float vA;
      void main(){ vC=color; vec4 mv = modelViewMatrix*vec4(position,1.); float pulse = 0.85+0.15*sin(uT*2.+position.x*3.+position.y*2.);
        gl_PointSize = uPx*size*pulse/(-mv.z); vA = pulse; gl_Position = projectionMatrix*mv; }`,
    fragmentShader: `uniform float uDark; varying vec3 vC; varying float vA;
      void main(){ vec2 p = gl_PointCoord-0.5; float d = length(p); if(d>0.5) discard;
        float core = smoothstep(0.18,0.0,d); float halo = smoothstep(0.5,0.0,d);
        vec3 c = mix(vC, vec3(1.), core*0.6*uDark);
        gl_FragColor = vec4(c, (core + halo*0.45)*vA); }`,
  });
}

/* ------------------------------------------------------------------ *
 * (b) 极光丝带 + 粒子场：三条品牌色丝带缓慢起伏，粒子漂浮，指针让丝带轻轻弯向光标。
 * ------------------------------------------------------------------ */
function buildAurora({ scene, camera, isDark, palette }: Ctx): Built {
  // 丝带 = 柔光面 + 若干条清晰的细丝线，两者用同一段位移函数，细丝线给出「清晰轮廓」，
  // 浅色主题下不再只是一团模糊的色雾。
  // 浅色：正常混合 + 加深、提饱和的颜色（白底上提亮会发灰）；深色：叠加混合，颜色略提亮。
  camera.position.set(0, 0, 10);
  camera.lookAt(0, 0, 0);
  const blend = isDark ? THREE.AdditiveBlending : THREE.NormalBlending;
  const tune = (c: THREE.Color) => {
    const hsl = { h: 0, s: 0, l: 0 };
    c.getHSL(hsl);
    return new THREE.Color().setHSL(hsl.h, Math.min(1, hsl.s * (isDark ? 1.15 : 1.25)), isDark ? Math.min(0.68, hsl.l * 1.15) : hsl.l * 0.92);
  };
  const cols = [tune(palette.a), tune(palette.b), tune(palette.c)];
  const cols2 = [cols[1], cols[2], cols[0]];
  const WAVE = `
    uniform float uT; uniform float uI; uniform vec2 uP;
    vec3 ribbon(vec3 p, vec2 uv, out float w){
      float x = p.x;
      w = sin(x*0.45 + uT*0.6 + uI*1.7)*0.9 + sin(x*0.9 - uT*0.4 + uI)*0.35 + sin(x*1.9 + uT*0.9)*0.08;
      w += uP.y*-0.6*exp(-pow(x/4. - uP.x*1.8, 2.));
      p.y += w + (uI-1.)*0.6; p.z += cos(x*0.35 + uT*0.3 + uI)*1.2;
      p.y += (uv.y-0.5)*(0.7+0.5*sin(x*0.6+uT*0.5+uI));
      return p;
    }`;
  const mats: THREE.ShaderMaterial[] = [];
  const geo = new THREE.PlaneGeometry(16, 1, 320, 24);
  cols.forEach((col, i) => {
    const m = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: blend,
      uniforms: { uT: { value: 0 }, uC: { value: col }, uC2: { value: cols2[i] }, uI: { value: i }, uP: { value: new THREE.Vector2() }, uK: { value: isDark ? 0.42 : 0.34 } },
      vertexShader: `${WAVE} varying vec2 vUv; varying float vH;
        void main(){ vUv = uv; float w; vec3 p = ribbon(position, uv, w); vH = w; gl_Position = projectionMatrix*modelViewMatrix*vec4(p,1.); }`,
      fragmentShader: `uniform vec3 uC; uniform vec3 uC2; uniform float uK; varying vec2 vUv; varying float vH;
        void main(){ float band = smoothstep(0.,0.3,vUv.y)*smoothstep(1.,0.7,vUv.y);
          float ends = smoothstep(0.,0.16,vUv.x)*smoothstep(1.,0.84,vUv.x);
          vec3 c = mix(uC, uC2, smoothstep(-1.,1.,vH));
          gl_FragColor = vec4(c, band*ends*uK); }`,
    });
    mats.push(m);
    scene.add(new THREE.Mesh(geo, m));

    // 细丝线：每条丝带 9 根，沿宽度分布，中间更亮
    const LINES = 9, SEG = 400;
    const pos: number[] = [], uvs: number[] = [];
    for (let l = 0; l < LINES; l++) {
      const v = 0.08 + (l / (LINES - 1)) * 0.84;
      for (let k = 0; k < SEG; k++) {
        for (const kk of [k, k + 1]) { const u = kk / SEG; pos.push(-8 + u * 16, v - 0.5, 0); uvs.push(u, v); }
      }
    }
    const lg = new THREE.BufferGeometry();
    lg.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    lg.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
    const lm = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: blend,
      uniforms: { uT: { value: 0 }, uC: { value: col }, uC2: { value: cols2[i] }, uI: { value: i }, uP: { value: new THREE.Vector2() }, uK: { value: isDark ? 0.75 : 0.6 } },
      vertexShader: `${WAVE} varying vec2 vUv; varying float vH;
        void main(){ vUv = uv; float w; vec3 p = ribbon(position, uv, w); vH = w; gl_Position = projectionMatrix*modelViewMatrix*vec4(p,1.); }`,
      fragmentShader: `uniform vec3 uC; uniform vec3 uC2; uniform float uK; uniform float uT; varying vec2 vUv; varying float vH;
        void main(){ float ends = smoothstep(0.,0.18,vUv.x)*smoothstep(1.,0.82,vUv.x);
          float mid = 1. - abs(vUv.y-0.5)*1.6;
          float shimmer = 0.7 + 0.3*sin(vUv.x*14. - uT*1.2 + vUv.y*9.);
          vec3 c = mix(uC, uC2, smoothstep(-1.,1.,vH));
          gl_FragColor = vec4(c, ends*mid*shimmer*uK); }`,
    });
    mats.push(lm);
    scene.add(new THREE.LineSegments(lg, lm));
  });
  scene.children.forEach((o) => (o.rotation.x = -0.25));

  // 粒子：浅色主题更小、更实（深色描点），深色主题更亮的小光点
  const N = 700;
  const pos = new Float32Array(N * 3), col = new Float32Array(N * 3), size = new Float32Array(N), seed = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    pos.set([(Math.random() - 0.5) * 18, (Math.random() - 0.5) * 5, (Math.random() - 0.5) * 5], i * 3);
    cols[i % 3].toArray(col, i * 3);
    size[i] = (isDark ? 0.3 : 0.22) + Math.random() * (isDark ? 0.55 : 0.3);
    seed[i] = Math.random();
  }
  const pg = new THREE.BufferGeometry();
  pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  pg.setAttribute("color", new THREE.BufferAttribute(col, 3));
  pg.setAttribute("size", new THREE.BufferAttribute(size, 1));
  pg.setAttribute("seed", new THREE.BufferAttribute(seed, 1));
  const pm = glowPoints(isDark, isDark ? 70 : 55);
  pm.vertexShader = pm.vertexShader
    .replace("attribute float size;", "attribute float size; attribute float seed;")
    .replace("vec4 mv = modelViewMatrix*vec4(position,1.);", "vec3 q = position; q.x = mod(q.x + uT*(0.15+seed*0.25) + 9., 18.) - 9.; q.y += sin(uT*0.5 + seed*6.28)*0.3; vec4 mv = modelViewMatrix*vec4(q,1.);");
  if (!isDark) {
    // 白底：实心小圆点、无大光晕，边缘抗锯齿
    pm.fragmentShader = `varying vec3 vC; varying float vA;
      void main(){ float d = length(gl_PointCoord-0.5); float a = smoothstep(0.5,0.32,d); if(a<=0.) discard; gl_FragColor = vec4(vC, a*0.85*vA); }`;
  }
  scene.add(new THREE.Points(pg, pm));

  return {
    update(t, px, py) {
      mats.forEach((m) => { m.uniforms.uT.value = t; m.uniforms.uP.value.set(px, py); });
      pm.uniforms.uT.value = t;
      camera.position.x = px * 0.4;
      camera.lookAt(0, 0, 0);
    },
    dispose() {},
  };
}

/* ------------------------------------------------------------------ *
 * (c) 风险地形：每条风险按（可能性, 影响）落在网格上，鼓起高度 = 分值；
 *     颜色从低风险的翠绿过渡到琥珀再到红；等高线 + 网格线 + 已处理风险会慢慢「下沉」。
 * ------------------------------------------------------------------ */
function buildTerrain({ scene, camera, isDark, palette }: Ctx, risks: RiskPoint[]): Built {
  camera.position.set(0, 6, 11.5);
  camera.lookAt(0, 0.4, 0);
  const pts = risks.length
    ? risks
    : [{ likelihood: 4, impact: 5, score: 20, open: true }, { likelihood: 3, impact: 5, score: 15, open: true }, { likelihood: 3, impact: 3, score: 9, open: true }, { likelihood: 2, impact: 4, score: 8, open: false }, { likelihood: 4, impact: 4, score: 16, open: true }, { likelihood: 2, impact: 3, score: 6, open: false }];
  const bumps = pts.slice(0, 16).map((r) => new THREE.Vector4((r.likelihood - 3) * 2.2, (3 - r.impact) * 1.3, (r.score / 25) * 2.3, r.open ? 1 : 0.45));
  while (bumps.length < 16) bumps.push(new THREE.Vector4(0, 0, 0, 0));

  const geo = new THREE.PlaneGeometry(14, 7, 280, 140);
  geo.rotateX(-Math.PI / 2);
  const mat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: true, side: THREE.DoubleSide,
    extensions: { derivatives: true } as never,
    uniforms: {
      uT: { value: 0 }, uB: { value: bumps }, uLow: { value: palette.b }, uMid: { value: palette.c }, uHigh: { value: palette.d }, uGrid: { value: palette.a },
      uDark: { value: isDark ? 1 : 0 }, uP: { value: new THREE.Vector2() },
    },
    vertexShader: `uniform float uT; uniform vec4 uB[16]; uniform vec2 uP; varying float vH; varying vec2 vXZ; varying float vFade;
      void main(){ vec3 p = position; float h = 0.;
        for(int i=0;i<16;i++){ vec4 b = uB[i]; float breathe = 0.88 + 0.12*sin(uT*0.9 + float(i)*1.3);
          float k = b.z * mix(1., breathe, b.w); h += k*exp(-((p.x-b.x)*(p.x-b.x)+(p.z-b.y)*(p.z-b.y))/1.6); }
        h += 0.08*sin(p.x*1.3 + uT*0.7)*cos(p.z*1.1 - uT*0.5);
        h += 0.35*exp(-((p.x-uP.x*5.)*(p.x-uP.x*5.)+(p.z-uP.y*2.5)*(p.z-uP.y*2.5))/1.2);
        p.y = h; vH = h; vXZ = p.xz;
        vFade = smoothstep(7.,4.5,abs(p.x))*smoothstep(3.5,2.,abs(p.z));
        gl_Position = projectionMatrix*modelViewMatrix*vec4(p,1.); }`,
    fragmentShader: `uniform vec3 uLow; uniform vec3 uMid; uniform vec3 uHigh; uniform vec3 uGrid; uniform float uDark; varying float vH; varying vec2 vXZ; varying float vFade;
      float line(float v, float w){ float d = fwidth(v); return 1. - smoothstep(0., d*w, abs(fract(v-0.5)-0.5)); }
      void main(){ float hn = clamp(vH/2.4, 0., 1.);
        vec3 c = hn < 0.5 ? mix(uLow, uMid, hn*2.) : mix(uMid, uHigh, (hn-0.5)*2.);
        float contour = line(vH*5., 1.2);
        float grid = max(line(vXZ.x*1.5, 1.), line(vXZ.y*1.5, 1.));
        float base = mix(0.10, 0.06, uDark) + hn*0.35;
        vec3 col = mix(c*mix(0.9,1.,uDark), vec3(1.), contour*0.25*uDark);
        float a = (base + contour*0.75 + grid*0.22) * vFade;
        gl_FragColor = vec4(mix(col, uGrid, grid*0.4*(1.-hn)), a); }`,
  });
  const mesh = new THREE.Mesh(geo, mat);
  scene.add(mesh);

  // 风险峰顶的光点
  const pos = new Float32Array(bumps.length * 3), col = new Float32Array(bumps.length * 3), size = new Float32Array(bumps.length);
  bumps.forEach((b, i) => {
    pos.set([b.x, b.z + 0.25, b.y], i * 3);
    const hn = b.z / 2.3;
    (hn > 0.6 ? palette.d : hn > 0.35 ? palette.c : palette.b).toArray(col, i * 3);
    size[i] = b.z > 0 ? 1.4 : 0;
  });
  const pg = new THREE.BufferGeometry();
  pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  pg.setAttribute("color", new THREE.BufferAttribute(col, 3));
  pg.setAttribute("size", new THREE.BufferAttribute(size, 1));
  const pm = glowPoints(isDark, 60);
  scene.add(new THREE.Points(pg, pm));

  const group = new THREE.Group();
  scene.remove(mesh); scene.add(group); group.add(mesh); group.add(scene.children.find((o) => o instanceof THREE.Points)!);

  return {
    update(t, px, py) {
      mat.uniforms.uT.value = t;
      mat.uniforms.uP.value.set(px, py);
      pm.uniforms.uT.value = t;
      group.rotation.y = Math.sin(t * 0.08) * 0.18 + px * 0.12;
      camera.position.y = 6 - py * 0.4;
      camera.lookAt(0, 0.4, 0);
    },
    dispose() {},
  };
}
