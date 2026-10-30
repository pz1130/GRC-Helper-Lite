// 登录页「地球转动」特效的多个候选版本（?globe=g1..g4 对比用，不带参数仍是原版 a）。
// 每个版本运动方式和构图都不同：
// g1 控制护盾：节点逐条连线，蜂窝护盾在球外成形 → 消散 → 再成形；
// g2 风险热力球：点阵大陆 + 扫描经线，风险点经过扫描线后由红转绿；
// g3 框架陀螺仪：三条倾斜轨道（框架）各带卫星异轴旋转，球体纬带流动；
// g4 分层全息：球由一摞纬度切片组成，切片随波浪错位旋转、像在「逐层核查」。
import * as THREE from "three";

export type GlobeVariant = "g1" | "g2" | "g3" | "g4";
type Ctx = { scene: THREE.Scene; camera: THREE.PerspectiveCamera; isDark: boolean; palette: Record<"a" | "b" | "c" | "d", THREE.Color> };
type Built = { update: (t: number, px: number, py: number) => void; dispose: () => void };

const DPR = () => Math.min(window.devicePixelRatio || 1, 2);
const blendOf = (d: boolean) => (d ? THREE.AdditiveBlending : THREE.NormalBlending);

function points(isDark: boolean, px: number, extra = "") {
  return new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: blendOf(isDark),
    uniforms: { uT: { value: 0 }, uPx: { value: px * DPR() } },
    vertexShader: `attribute float size; attribute vec3 color; attribute float alpha; uniform float uT; uniform float uPx; varying vec3 vC; varying float vA;
      void main(){ vC=color; vA=alpha; vec4 mv=modelViewMatrix*vec4(position,1.); vec3 n=normalize((modelViewMatrix*vec4(position,0.)).xyz);
        ${extra || "float face=1.;"} vA*=face; gl_PointSize=uPx*size/(-mv.z); gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `varying vec3 vC; varying float vA; void main(){ float d=length(gl_PointCoord-.5); float a=smoothstep(.5,.15,d); if(a<.01) discard; gl_FragColor=vec4(vC,a*vA); }`,
  });
}
const FRONT = "float face=smoothstep(-.35,.25,n.z)*.85+.15;";

function fib(n: number, r: number) {
  const out: THREE.Vector3[] = [];
  for (let i = 0; i < n; i++) { const y = 1 - (i / (n - 1)) * 2, rr = Math.sqrt(1 - y * y), th = i * 2.399963; out.push(new THREE.Vector3(Math.cos(th) * rr, y, Math.sin(th) * rr).multiplyScalar(r)); }
  return out;
}
// 伪大陆：几团球面高斯叠加，够看出「这是地球」
const BLOBS = [[0.3, 0.8, 0.9], [-0.6, 0.3, 0.7], [0.5, -0.2, -0.8], [-0.2, -0.7, -0.5], [0.9, 0.3, -0.2], [-0.8, -0.1, -0.6], [0.1, 0.5, -0.85]].map((v) => new THREE.Vector3(...v).normalize());
const land = (v: THREE.Vector3) => { const u = v.clone().normalize(); let s = 0; for (const b of BLOBS) s += Math.exp((u.dot(b) - 1) * 7); return s + 0.25 * Math.sin(u.x * 9) * Math.sin(u.y * 7) * Math.sin(u.z * 8); };

function geo(attrs: Record<string, [number[] | Float32Array, number]>) {
  const g = new THREE.BufferGeometry();
  for (const [k, [a, n]] of Object.entries(attrs)) g.setAttribute(k, new THREE.BufferAttribute(a instanceof Float32Array ? a : new Float32Array(a), n));
  return g;
}
function fresnel(R: number, a: THREE.Color, b: THREE.Color, k: number, isDark: boolean) {
  return new THREE.Mesh(new THREE.SphereGeometry(R, 80, 56), new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: blendOf(isDark),
    uniforms: { uA: { value: a }, uB: { value: b }, uK: { value: k } },
    vertexShader: `varying vec3 vN; varying vec3 vV; varying float vY; void main(){ vec4 mv=modelViewMatrix*vec4(position,1.); vN=normalize(normalMatrix*normal); vV=normalize(-mv.xyz); vY=position.y; gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `uniform vec3 uA; uniform vec3 uB; uniform float uK; varying vec3 vN; varying vec3 vV; varying float vY; void main(){ float f=pow(1.-abs(dot(vN,vV)),2.4); gl_FragColor=vec4(mix(uA,uB,smoothstep(-2.,2.,vY)), f*uK); }`,
  }));
}

/* g1 控制护盾 */
function g1({ scene, camera, isDark, palette }: Ctx): Built {
  camera.position.set(0, 0.3, 11.5); camera.lookAt(0, 0, 0);
  const R = 2.3, root = new THREE.Group(); root.rotation.z = 0.25; scene.add(root);
  root.add(fresnel(R, palette.a, palette.b, isDark ? 0.8 : 0.5, isDark));
  // 点阵大陆
  const P: number[] = [], C: number[] = [], S: number[] = [], A: number[] = [];
  for (const v of fib(2600, R)) if (land(v) > 0.55) { P.push(...v.toArray()); C.push(palette.a.r, palette.a.g, palette.a.b); S.push(0.9); A.push(isDark ? 0.6 : 0.75); }
  const dm = points(isDark, 60, FRONT); root.add(new THREE.Points(geo({ position: [P, 3], color: [C, 3], size: [S, 1], alpha: [A, 1] }), dm));
  // 控制节点 + 依次点亮的连线
  const nodes = fib(18, R * 1.01);
  const nm = points(isDark, 140, FRONT);
  root.add(new THREE.Points(geo({ position: [nodes.flatMap((v) => v.toArray()), 3], color: [nodes.flatMap(() => palette.b.toArray()), 3], size: [nodes.map((_, i) => (i % 4 ? 1 : 1.6)), 1], alpha: [nodes.map(() => 1), 1] }), nm));
  const lp: number[] = [], lt: number[] = [], lo: number[] = [];
  let k = 0;
  nodes.forEach((a, i) => nodes.forEach((b, j) => { if (j <= i || a.distanceTo(b) > R * 0.95) return; const pts = new THREE.QuadraticBezierCurve3(a, a.clone().add(b).multiplyScalar(0.5).setLength(R * 1.08), b).getPoints(24); const o = (k++ * 0.071) % 1; for (let p = 0; p < 24; p++) { lp.push(...pts[p].toArray(), ...pts[p + 1].toArray()); lt.push(p / 24, (p + 1) / 24); lo.push(o, o); } }));
  const lm = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: blendOf(isDark), uniforms: { uP: { value: 0 }, uC: { value: palette.b } },
    vertexShader: `attribute float t; attribute float o; varying float vT; varying float vO; void main(){ vT=t; vO=o; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
    fragmentShader: `uniform float uP; uniform vec3 uC; varying float vT; varying float vO; void main(){ float grow=clamp((uP-vO)*4.,0.,1.); if(vT>grow) discard; gl_FragColor=vec4(uC,.75); }` });
  root.add(new THREE.LineSegments(geo({ position: [lp, 3], t: [lt, 1], o: [lo, 1] }), lm));
  // 蜂窝护盾（二十面体细分的线框，按「成形进度」从底部向上显现）
  const ico = new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(R * 1.28, 3));
  const sm = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: blendOf(isDark), uniforms: { uP: { value: 0 }, uT: { value: 0 }, uC: { value: palette.b }, uR: { value: R * 1.28 } },
    vertexShader: `varying vec3 vP; void main(){ vP=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
    fragmentShader: `uniform float uP; uniform float uT; uniform vec3 uC; uniform float uR; varying vec3 vP; void main(){ float h=(vP.y/uR+1.)*.5; float edge=uP*1.2-h; if(edge<0.) discard; float glow=exp(-edge*10.)*.9; float wave=.5+.5*sin(vP.y*3.-uT*3.); gl_FragColor=vec4(uC, min(1.,(.22+.18*wave)+glow)); }` });
  const shield = new THREE.LineSegments(ico, sm); scene.add(shield);
  return { update(t, px, py) {
    root.rotation.y = t * 0.16 + px * 0.25; root.rotation.x = py * 0.1; shield.rotation.y = -t * 0.06;
    const cyc = (t % 10) / 10; // 0-.45 连线, .35-.8 护盾成形, .85-1 消散
    lm.uniforms.uP.value = Math.min(1.25, cyc * 2.8);
    sm.uniforms.uP.value = cyc < 0.35 ? 0 : cyc < 0.8 ? (cyc - 0.35) / 0.45 : Math.max(0, 1 - (cyc - 0.8) / 0.2);
    sm.uniforms.uT.value = t; dm.uniforms.uT.value = t;
  }, dispose() {} };
}

/* g2 风险热力球 */
function g2({ scene, camera, isDark, palette }: Ctx): Built {
  camera.position.set(0, 1.2, 11); camera.lookAt(0, 0, 0);
  const R = 2.5, root = new THREE.Group(); scene.add(root);
  root.add(fresnel(R, palette.a, palette.a, isDark ? 0.6 : 0.35, isDark));
  const pts = fib(5200, R).filter((v) => land(v) > 0.5);
  const P = new Float32Array(pts.flatMap((v) => v.toArray()));
  const risk = pts.map((v) => Math.max(0, Math.sin(v.x * 2.1 + 1) * Math.cos(v.z * 1.7) + Math.sin(v.y * 3)) * 0.7);
  const C = new Float32Array(pts.length * 3), S = new Float32Array(pts.length), A = new Float32Array(pts.length).fill(1);
  const m = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: blendOf(isDark),
    uniforms: { uT: { value: 0 }, uPx: { value: 55 * DPR() }, uLo: { value: isDark ? new THREE.Color("#3f7d62") : new THREE.Color("#7e9a7a") }, uHi: { value: palette.d }, uMid: { value: palette.c }, uBase: { value: isDark ? new THREE.Color("#5c7f99") : new THREE.Color("#5c7f99") } },
    vertexShader: `attribute float risk; uniform float uT; uniform float uPx; uniform vec3 uLo; uniform vec3 uHi; uniform vec3 uMid; uniform vec3 uBase; varying vec3 vC; varying float vA;
      void main(){ vec4 wp=modelMatrix*vec4(position,1.); float lon=atan(wp.x,wp.z); float scan=mod(lon-uT+3.14159,6.28318)-3.14159;
        float fx=smoothstep(0.,.6,-scan)*(1.-smoothstep(2.4,3.1,-scan)); float r=risk; vec3 hot=mix(uMid,uHi,clamp(r*1.4-.3,0.,1.));
        vec3 c=r<.15?uBase:mix(hot,uLo,fx); float flash=exp(-abs(scan)*12.); c=mix(c,vec3(1.),flash*.5);
        vec4 mv=viewMatrix*wp; vec3 n=normalize((viewMatrix*vec4(normalize(wp.xyz),0.)).xyz); vC=c; vA=(smoothstep(-.3,.3,n.z)*.85+.15);
        float s=r<.15?1.:1.+r*1.6*(1.-fx*.5); gl_PointSize=uPx*s*(1.+flash)/(-mv.z); gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `varying vec3 vC; varying float vA; void main(){ float d=length(gl_PointCoord-.5); float a=smoothstep(.5,.15,d); if(a<.01) discard; gl_FragColor=vec4(vC,a*vA); }` });
  const g = geo({ position: [P, 3], color: [C, 3], size: [S, 1], alpha: [A, 1], risk: [risk, 1] });
  root.add(new THREE.Points(g, m));
  // 扫描经线（不随地球转，固定在相机前方偏左）
  const scanG = new THREE.Mesh(new THREE.SphereGeometry(R * 1.02, 64, 32, 0.5 + Math.PI / 2 - 0.03, 0.06), new THREE.MeshBasicMaterial({ color: palette.b, transparent: true, opacity: isDark ? 0.35 : 0.3, side: THREE.DoubleSide, blending: blendOf(isDark), depthWrite: false }));
  scene.add(scanG);
  // 底部「风险等级」光环
  const ring = new THREE.Mesh(new THREE.RingGeometry(R * 1.15, R * 1.18, 128), new THREE.MeshBasicMaterial({ color: palette.c, transparent: true, opacity: 0.4, side: THREE.DoubleSide, depthWrite: false }));
  ring.rotation.x = -Math.PI / 2.3; ring.position.y = -R * 0.2; scene.add(ring);
  return { update(t, px, py) {
    // 地球匀速自转，扫描经线固定在左前方：风险点转过扫描线后由红转绿，转到背面再「复发」
    root.rotation.y = t * 0.3 + px * 0.2; root.rotation.x = py * 0.08;
    m.uniforms.uT.value = 0.5; ring.scale.setScalar(1 + 0.03 * Math.sin(t * 2));
  }, dispose() {} };
}

/* g3 框架陀螺仪 */
function g3({ scene, camera, isDark, palette }: Ctx): Built {
  camera.position.set(0, 0.6, 12.5); camera.lookAt(0, 0, 0);
  const R = 1.9; const core = new THREE.Group(); scene.add(core);
  core.add(fresnel(R, palette.b, palette.a, isDark ? 0.9 : 0.55, isDark));
  const bandM = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: blendOf(isDark), uniforms: { uT: { value: 0 }, uC: { value: palette.a } },
    vertexShader: `varying vec3 vP; varying vec3 vN; varying vec3 vV; void main(){ vP=position; vec4 mv=modelViewMatrix*vec4(position,1.); vN=normalize(normalMatrix*normal); vV=normalize(-mv.xyz); gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `uniform float uT; uniform vec3 uC; varying vec3 vP; varying vec3 vN; varying vec3 vV; void main(){ float lat=vP.y; float lon=atan(vP.x,vP.z);
      float band=smoothstep(.85,1.,sin(lat*9.+uT*1.2)); float mer=smoothstep(.96,1.,sin(lon*8.-uT*.6)); float face=.25+.75*abs(dot(vN,vV));
      gl_FragColor=vec4(uC,(band*.55+mer*.35)*face); }` });
  core.add(new THREE.Mesh(new THREE.SphereGeometry(R * 1.003, 96, 64), bandM));
  const rings: { g: THREE.Group; sat: THREE.Mesh; speed: number; r: number }[] = [];
  const cols = [palette.a, palette.b, palette.c];
  [[1.55, 0.3, 0.2], [1.85, -0.9, 0.6], [2.15, 1.2, -0.5]].forEach(([k, rx, rz], i) => {
    const g = new THREE.Group(); g.rotation.set(rx, 0, rz); scene.add(g); const r = R * k;
    g.add(new THREE.Mesh(new THREE.TorusGeometry(r, 0.012, 6, 220), new THREE.MeshBasicMaterial({ color: cols[i], transparent: true, opacity: isDark ? 0.55 : 0.6, blending: blendOf(isDark), depthWrite: false })));
    // 刻度
    const ticks: number[] = []; for (let s = 0; s < 60; s++) { const a = (s / 60) * Math.PI * 2, l = s % 5 ? 0.06 : 0.14; ticks.push(Math.cos(a) * r, Math.sin(a) * r, 0, Math.cos(a) * (r + l), Math.sin(a) * (r + l), 0); }
    g.add(new THREE.LineSegments(geo({ position: [ticks, 3] }), new THREE.LineBasicMaterial({ color: cols[i], transparent: true, opacity: 0.45 })));
    const sat = new THREE.Mesh(new THREE.OctahedronGeometry(0.13), new THREE.MeshBasicMaterial({ color: cols[i] })); g.add(sat);
    const halo = new THREE.Mesh(new THREE.RingGeometry(0.18, 0.22, 32), new THREE.MeshBasicMaterial({ color: cols[i], transparent: true, opacity: 0.6, side: THREE.DoubleSide })); sat.add(halo);
    rings.push({ g, sat, speed: [0.5, -0.35, 0.25][i], r });
  });
  return { update(t, px, py) {
    core.rotation.y = t * 0.25 + px * 0.2; core.rotation.x = py * 0.1; bandM.uniforms.uT.value = t;
    rings.forEach((o, i) => { o.g.rotation.y = t * 0.08 * (i + 1) * (i % 2 ? -1 : 1) + px * 0.15; const a = t * o.speed + i * 2; o.sat.position.set(Math.cos(a) * o.r, Math.sin(a) * o.r, 0); o.sat.rotation.set(t, t * 1.3, 0); o.sat.scale.setScalar(1 + 0.15 * Math.sin(t * 3 + i)); });
  }, dispose() {} };
}

/* g4 分层全息 */
function g4({ scene, camera, isDark, palette }: Ctx): Built {
  camera.position.set(0, 2.6, 12); camera.lookAt(0, 0, 0);
  const R = 2.5, root = new THREE.Group(); scene.add(root);
  const L = 22; const slices: THREE.Group[] = [];
  for (let i = 0; i < L; i++) {
    const y = -R + ((i + 0.5) / L) * 2 * R, r = Math.sqrt(R * R - y * y); const g = new THREE.Group(); g.position.y = y; root.add(g);
    const c = palette.a.clone().lerp(palette.b, i / L);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(r, 0.012, 4, 120), new THREE.MeshBasicMaterial({ color: c, transparent: true, opacity: isDark ? 0.6 : 0.7, blending: blendOf(isDark), depthWrite: false }));
    ring.rotation.x = Math.PI / 2; g.add(ring);
    // 每层上的「大陆」点
    const P: number[] = [], C: number[] = [], S: number[] = [], A: number[] = [];
    const n = Math.round(r * 40);
    for (let k = 0; k < n; k++) { const a = (k / n) * Math.PI * 2, v = new THREE.Vector3(Math.cos(a) * r, y, Math.sin(a) * r); if (land(v) > 0.5) { P.push(Math.cos(a) * r, 0, Math.sin(a) * r); C.push(...c.toArray()); S.push(1); A.push(0.9); } }
    if (P.length) g.add(new THREE.Points(geo({ position: [P, 3], color: [C, 3], size: [S, 1], alpha: [A, 1] }), points(isDark, 70)));
    slices.push(g);
  }
  // 扫描平面
  const scan = new THREE.Mesh(new THREE.CircleGeometry(R * 1.25, 96), new THREE.MeshBasicMaterial({ color: palette.b, transparent: true, opacity: isDark ? 0.12 : 0.1, side: THREE.DoubleSide, depthWrite: false, blending: blendOf(isDark) }));
  scan.rotation.x = -Math.PI / 2; root.add(scan);
  const scanEdge = new THREE.Mesh(new THREE.TorusGeometry(R * 1.25, 0.02, 6, 160), new THREE.MeshBasicMaterial({ color: palette.b, transparent: true, opacity: 0.8 })); scanEdge.rotation.x = Math.PI / 2; scanEdge.rotation.x = 0; scan.add(scanEdge);
  // 底座
  const base = new THREE.Mesh(new THREE.RingGeometry(R * 0.9, R * 1.35, 96), new THREE.MeshBasicMaterial({ color: palette.a, transparent: true, opacity: isDark ? 0.18 : 0.14, side: THREE.DoubleSide, depthWrite: false }));
  base.rotation.x = -Math.PI / 2; base.position.y = -R - 0.4; scene.add(base);
  return { update(t, px, py) {
    root.rotation.x = py * 0.08; root.rotation.z = px * 0.06;
    const sy = Math.sin(t * 0.7) * R * 0.95; scan.position.y = sy;
    slices.forEach((g, i) => { const d = g.position.y - sy; const push = Math.exp(-d * d * 3); g.rotation.y = t * 0.2 + Math.sin(t * 0.8 + i * 0.35) * 0.25 + push * 0.6; g.scale.setScalar(1 + push * 0.08); });
    base.rotation.z = t * 0.1;
  }, dispose() {} };
}

export function buildGlobeVariant(v: GlobeVariant, ctx: Ctx): Built {
  return ({ g1, g2, g3, g4 } as const)[v](ctx);
}
