// 第二批登录页地球候选（?globe=h1..h4）。大陆轮廓来自 Natural Earth 110m（公有领域），栅格成 land-mask.png。
// h1 写实地球：真实大陆 + 晨昏线 + 独立转动的云层 + 大气辉光；
// h2 纸艺低多边形：平面着色的多面体地球，陆地凸起，一架小飞机拖着虚线绕飞；
// h3 地图卷曲：平铺的世界地图卷成地球转一圈，再展开成地图，循环；
// h4 洋流粒子：上千个粒子沿纬向「洋流」流动，覆盖在淡色大陆地球上。
import * as THREE from "three";
import maskUrl from "./land-mask.png";

export type GlobeVariant2 = "h1" | "h2" | "h3" | "h4";
type Ctx = { scene: THREE.Scene; camera: THREE.PerspectiveCamera; isDark: boolean; palette: Record<"a" | "b" | "c" | "d", THREE.Color> };
type Built = { update: (t: number, px: number, py: number) => void; dispose: () => void };
const DPR = () => Math.min(window.devicePixelRatio || 1, 2);

// 调色：雾蓝海 + 鼠尾草绿陆（浅色）/ 北欧石板 + 墨绿（深色），避开霓虹紫
const pal = (d: boolean) => d
  ? { sea: new THREE.Color("#3a5568"), sea2: new THREE.Color("#5a7a8e"), land: new THREE.Color("#3f7d62"), land2: new THREE.Color("#6fa487"), rim: new THREE.Color("#7fb0c9"), ink: new THREE.Color("#cfe3da") }
  : { sea: new THREE.Color("#8fb0c6"), sea2: new THREE.Color("#c4d8e4"), land: new THREE.Color("#7e9a7a"), land2: new THREE.Color("#b5c9a8"), rim: new THREE.Color("#9cc3dc"), ink: new THREE.Color("#3e5a6e") };

const maskTex = () => { const t = new THREE.TextureLoader().load(maskUrl); t.colorSpace = THREE.NoColorSpace; t.wrapS = THREE.RepeatWrapping; return t; };
let maskData: Promise<(u: number, v: number) => number> | null = null;
function maskSampler() {
  maskData ??= new Promise((res) => { const im = new Image(); im.onload = () => { const c = document.createElement("canvas"); c.width = im.width; c.height = im.height; const g = c.getContext("2d")!; g.drawImage(im, 0, 0); const d = g.getImageData(0, 0, c.width, c.height).data;
    res((u, v) => { const x = Math.min(c.width - 1, Math.max(0, Math.floor(((u % 1) + 1) % 1 * c.width))), y = Math.min(c.height - 1, Math.max(0, Math.floor((1 - v) * c.height))); return d[(y * c.width + x) * 4] / 255; }); }; im.src = maskUrl; });
  return maskData;
}
// three 的球面 uv：x=-cosφ·sinθ, z=sinφ·sinθ, u=φ/2π, v=1-θ/π
const uvOf = (p: THREE.Vector3) => { const r = p.length(); let phi = Math.atan2(p.z, -p.x); if (phi < 0) phi += Math.PI * 2; return [phi / (Math.PI * 2), 1 - Math.acos(p.y / r) / Math.PI] as const; };

function atmosphere(R: number, col: THREE.Color, k: number, isDark: boolean) {
  return new THREE.Mesh(new THREE.SphereGeometry(R * 1.12, 64, 48), new THREE.ShaderMaterial({ transparent: true, depthWrite: false, side: THREE.BackSide, blending: isDark ? THREE.AdditiveBlending : THREE.NormalBlending,
    uniforms: { uC: { value: col }, uK: { value: k } },
    vertexShader: `varying vec3 vN; varying vec3 vV; void main(){ vec4 mv=modelViewMatrix*vec4(position,1.); vN=normalize(normalMatrix*normal); vV=normalize(-mv.xyz); gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `uniform vec3 uC; uniform float uK; varying vec3 vN; varying vec3 vV; void main(){ float f=pow(clamp(1.-abs(dot(vN,vV)),0.,1.),1.6); float edge=smoothstep(0.,.35,abs(dot(vN,vV))); gl_FragColor=vec4(uC,f*edge*uK); }` }));
}

/* h1 写实地球 */
function h1({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, 0, 10.5); camera.lookAt(0, 0, 0);
  const R = 2.6, c = pal(isDark), tilt = new THREE.Group(); tilt.rotation.z = 0.41; scene.add(tilt);
  const mask = maskTex();
  const earthM = new THREE.ShaderMaterial({ uniforms: { uM: { value: mask }, uSea: { value: c.sea }, uSea2: { value: c.sea2 }, uLand: { value: c.land }, uLand2: { value: c.land2 }, uSun: { value: new THREE.Vector3(-0.6, 0.35, 0.75).normalize() }, uNight: { value: isDark ? 0.5 : 0.6 }, uT: { value: 0 } },
    vertexShader: `varying vec2 vUv; varying vec3 vWN; varying vec3 vN; varying vec3 vV; void main(){ vUv=uv; vWN=normalize(mat3(modelMatrix)*normal); vec4 mv=modelViewMatrix*vec4(position,1.); vN=normalize(normalMatrix*normal); vV=normalize(-mv.xyz); gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `uniform sampler2D uM; uniform vec3 uSea; uniform vec3 uSea2; uniform vec3 uLand; uniform vec3 uLand2; uniform vec3 uSun; uniform float uNight; uniform float uT; varying vec2 vUv; varying vec3 vWN; varying vec3 vN; varying vec3 vV;
      float m(vec2 o){ return texture2D(uM, vUv+o).r; }
      void main(){ float l=(m(vec2(0.))*4.+m(vec2(.002,0.))+m(vec2(-.002,0.))+m(vec2(0.,.003))+m(vec2(0.,-.003)))/8.;
        float lat=abs(vUv.y-.5)*2.; vec3 land=mix(uLand,uLand2,smoothstep(.2,.9,lat)+.15*sin(vUv.x*80.)*sin(vUv.y*60.)); land=mix(land,vec3(.96),smoothstep(.82,.92,lat));
        float shore=smoothstep(.05,.45,l)*(1.-smoothstep(.45,.9,l)); vec3 sea=mix(uSea,uSea2,shore*1.4+.25*smoothstep(.0,1.,lat));
        vec3 col=mix(sea,land,smoothstep(.45,.55,l));
        float d=dot(vWN,uSun); float day=smoothstep(-.25,.35,d); col*=mix(uNight,1.08,day);
        float spec=pow(max(0.,dot(reflect(-uSun,vWN),vec3(0.,0.,1.))),24.)*(1.-step(.5,l))*.35; col+=spec;
        float rim=pow(1.-max(0.,dot(vN,vV)),3.); col=mix(col,vec3(.85,.93,1.),rim*.35);
        gl_FragColor=vec4(col,1.); }` });
  const earth = new THREE.Mesh(new THREE.SphereGeometry(R, 128, 96), earthM); tilt.add(earth);
  // 云层：程序噪声，比地球转得快一点
  const cloudM = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, uniforms: { uT: { value: 0 }, uSun: earthM.uniforms.uSun, uA: { value: isDark ? 0.35 : 0.6 } },
    vertexShader: `varying vec3 vP; varying vec3 vWN; void main(){ vP=position; vWN=normalize(mat3(modelMatrix)*normal); gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
    fragmentShader: `uniform float uT; uniform vec3 uSun; uniform float uA; varying vec3 vP; varying vec3 vWN;
      float h(vec3 p){ return fract(sin(dot(p,vec3(127.1,311.7,74.7)))*43758.5453); }
      float n(vec3 p){ vec3 i=floor(p), f=fract(p); f=f*f*(3.-2.*f); return mix(mix(mix(h(i),h(i+vec3(1,0,0)),f.x),mix(h(i+vec3(0,1,0)),h(i+vec3(1,1,0)),f.x),f.y),mix(mix(h(i+vec3(0,0,1)),h(i+vec3(1,0,1)),f.x),mix(h(i+vec3(0,1,1)),h(i+vec3(1,1,1)),f.x),f.y),f.z); }
      void main(){ vec3 p=vP*1.1+vec3(uT*.05,0.,0.); float v=n(p)*.55+n(p*2.1)*.3+n(p*4.3)*.15; float a=smoothstep(.55,.75,v); float day=smoothstep(-.2,.4,dot(vWN,uSun));
        gl_FragColor=vec4(vec3(1.),a*uA*(.35+.65*day)); }` });
  const clouds = new THREE.Mesh(new THREE.SphereGeometry(R * 1.015, 96, 72), cloudM); tilt.add(clouds);
  scene.add(atmosphere(R, c.rim, isDark ? 0.9 : 0.8, isDark));
  return { update(t, px, py) { earth.rotation.y = -1.9 + t * 0.12 + px * 0.2; clouds.rotation.y = -1.9 + t * 0.16 + px * 0.2; tilt.rotation.x = py * 0.08; cloudM.uniforms.uT.value = t; }, dispose() {} };
}

/* h2 纸艺低多边形 */
function h2({ scene, camera, isDark, palette }: Ctx): Built {
  camera.position.set(0, 0.8, 11); camera.lookAt(0, 0, 0);
  const R = 2.4, c = pal(isDark), root = new THREE.Group(); root.rotation.z = 0.3; scene.add(root);
  scene.add(new THREE.HemisphereLight(isDark ? "#c8d8e8" : "#ffffff", isDark ? "#1c242b" : "#b9c6cf", isDark ? 1.1 : 1.5));
  const sun = new THREE.DirectionalLight("#fff4e2", isDark ? 1.6 : 1.8); sun.position.set(-4, 5, 6); scene.add(sun);
  const geo = new THREE.IcosahedronGeometry(R, 9).toNonIndexed(); // 平面着色：每个三角形独立
  const mat = new THREE.MeshStandardMaterial({ vertexColors: true, flatShading: true, roughness: 0.9, metalness: 0 });
  const mesh = new THREE.Mesh(geo, mat); root.add(mesh);
  const pos = geo.getAttribute("position") as THREE.BufferAttribute; const col = new Float32Array(pos.count * 3); geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  const fill = (land: (u: number, v: number) => number) => {
    const a = new THREE.Vector3(), b = new THREE.Vector3(), d = new THREE.Vector3();
    for (let i = 0; i < pos.count; i += 3) {
      a.fromBufferAttribute(pos, i); b.fromBufferAttribute(pos, i + 1); d.fromBufferAttribute(pos, i + 2);
      const ctr = a.clone().add(b).add(d).divideScalar(3); const [u, v] = uvOf(ctr); const L = land(u, v) > 0.5; const lat = Math.abs(v - 0.5) * 2;
      const jitter = ((i * 9301 + 49297) % 233280) / 233280;
      const cc = L ? (lat > 0.85 ? new THREE.Color("#f2f4f2") : c.land.clone().lerp(c.land2, jitter * 0.6 + lat * 0.3)) : c.sea2.clone().lerp(c.sea, 0.35 + jitter * 0.3);
      const s = L ? 1.035 + jitter * 0.02 : 1;
      for (let k = 0; k < 3; k++) { const p = new THREE.Vector3().fromBufferAttribute(pos, i + k).setLength(R * s); pos.setXYZ(i + k, p.x, p.y, p.z); cc.toArray(col, (i + k) * 3); }
    }
    pos.needsUpdate = true; (geo.getAttribute("color") as THREE.BufferAttribute).needsUpdate = true; geo.computeVertexNormals();
  };
  for (let i = 0; i < pos.count; i++) c.sea2.toArray(col, i * 3);
  void maskSampler().then(fill);
  // 小飞机 + 虚线航迹（绕倾斜大圆飞）
  const orbit = new THREE.Group(); orbit.rotation.set(0.6, 0, -0.35); scene.add(orbit);
  const plane = new THREE.Group(); orbit.add(plane);
  const pm = new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.5, flatShading: true }), am = new THREE.MeshStandardMaterial({ color: palette.c, roughness: 0.6, flatShading: true });
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.07, 0.32, 4, 8), pm); body.rotation.z = Math.PI / 2; plane.add(body);
  const wing = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.02, 0.5), am); plane.add(wing);
  const tail = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.16, 0.02), am); tail.position.set(-0.2, 0.08, 0); plane.add(tail);
  const RO = R * 1.25, N = 90, tp = new Float32Array(N * 3);
  const trail = new THREE.Points(new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(tp, 3)), new THREE.PointsMaterial({ color: isDark ? "#e8eef2" : "#5c7f99", size: 0.05, transparent: true, opacity: 0.8 }));
  orbit.add(trail);
  return { update(t, px, py) {
    root.rotation.y = -1.9 + t * 0.15 + px * 0.25; root.rotation.x = py * 0.08;
    const a = t * 0.55; plane.position.set(Math.cos(a) * RO, 0, -Math.sin(a) * RO); plane.rotation.y = a + Math.PI; plane.position.y = Math.sin(t * 2) * 0.05;
    for (let i = 0; i < N; i++) { const b = a - (i + 4) * 0.035; tp[i * 3] = Math.cos(b) * RO; tp[i * 3 + 1] = 0; tp[i * 3 + 2] = -Math.sin(b) * RO; }
    trail.geometry.attributes.position.needsUpdate = true; (trail.material as THREE.PointsMaterial).size = 0.05;
  }, dispose() {} };
}

/* h3 地图卷曲 */
function h3({ scene, camera, isDark, palette }: Ctx): Built {
  camera.position.set(0, 0, 11); camera.lookAt(0, 0, 0);
  const R = 2.3, c = pal(isDark);
  const g = new THREE.SphereGeometry(R, 160, 80); const uv = g.getAttribute("uv");
  const flat = new Float32Array(uv.count * 3); const W = Math.PI * R * 1.1, H = W / 2;
  for (let i = 0; i < uv.count; i++) { flat[i * 3] = (uv.getX(i) - 0.5) * W; flat[i * 3 + 1] = (uv.getY(i) - 0.5) * H; flat[i * 3 + 2] = 0; }
  g.setAttribute("aFlat", new THREE.BufferAttribute(flat, 3));
  const m = new THREE.ShaderMaterial({ side: THREE.DoubleSide, transparent: true,
    uniforms: { uM: { value: maskTex() }, uK: { value: 1 }, uRot: { value: 0 }, uT: { value: 0 }, uSea: { value: c.sea }, uSea2: { value: c.sea2 }, uLand: { value: c.land }, uLand2: { value: c.land2 }, uInk: { value: c.ink }, uPin: { value: palette.c } },
    vertexShader: `attribute vec3 aFlat; uniform float uK; uniform float uRot; varying vec2 vUv; varying float vShade;
      void main(){ vUv=uv; float c=cos(uRot), s=sin(uRot); vec3 sp=vec3(c*position.x+s*position.z, position.y, -s*position.x+c*position.z);
        // 展开时沿 x 方向先后卷起：像一张纸从两边卷成圆筒再收成球
        float k=clamp(uK*1.6-abs(aFlat.x)/${(W / 2).toFixed(3)}*.6,0.,1.); k=k*k*(3.-2.*k);
        vec3 p=mix(aFlat,sp,k); vShade=mix(1.,.55+.45*max(0.,normalize(sp).z),k); gl_Position=projectionMatrix*modelViewMatrix*vec4(p,1.); }`,
    fragmentShader: `uniform sampler2D uM; uniform vec3 uSea; uniform vec3 uSea2; uniform vec3 uLand; uniform vec3 uLand2; uniform vec3 uInk; uniform vec3 uPin; uniform float uT; varying vec2 vUv; varying float vShade;
      void main(){ float l=texture2D(uM,vUv).r; vec3 sea=mix(uSea,uSea2,.5+.5*sin(vUv.y*3.14159)); vec3 col=mix(sea,mix(uLand,uLand2,vUv.y),smoothstep(.4,.6,l));
        vec2 gr=abs(fract(vUv*vec2(24.,12.))-.5); float grid=1.-smoothstep(0.,.04,min(gr.x,gr.y*2.)*.5); col=mix(col,uInk,grid*.18);
        float coast=smoothstep(.2,.5,l)*(1.-smoothstep(.5,.8,l)); col=mix(col,uInk,coast*.5);
        gl_FragColor=vec4(col*vShade,1.); }` });
  const mesh = new THREE.Mesh(g, m); mesh.rotation.x = 0.12; scene.add(mesh);
  // 地图状态时的定位钉（风险点）
  const pins = new THREE.Group(); scene.add(pins);
  const spots = [[0.53, 0.72], [0.3, 0.66], [0.78, 0.62], [0.58, 0.35], [0.35, 0.4], [0.85, 0.3]];
  const pinMat = new THREE.MeshBasicMaterial({ color: palette.c, transparent: true }), ringMat = new THREE.MeshBasicMaterial({ color: palette.c, transparent: true, side: THREE.DoubleSide });
  const pinObjs = spots.map(([u, v]) => { const p = new THREE.Group(); p.position.set((u - 0.5) * W, (v - 0.5) * H, 0.05); const dot = new THREE.Mesh(new THREE.CircleGeometry(0.09, 24), pinMat); const ring = new THREE.Mesh(new THREE.RingGeometry(0.12, 0.16, 32), ringMat.clone()); p.add(dot, ring); pins.add(p); return { p, ring }; });
  pins.rotation.x = 0.12;
  return { update(t, px, py) {
    const T = 12, x = t % T; // 0-4.5 球转, 4.5-6 展开, 6-10 地图, 10-12 卷回
    const ss = (a: number, b: number) => { const k = Math.min(1, Math.max(0, (x - a) / (b - a))); return k * k * (3 - 2 * k); };
    const k = x < 4.5 ? 1 : x < 6 ? 1 - ss(4.5, 6) : x < 10 ? 0 : ss(10, 12);
    m.uniforms.uK.value = k; m.uniforms.uRot.value = -1.9 + t * 0.35; m.uniforms.uT.value = t;
    mesh.rotation.y = px * 0.15 * k + px * 0.05; mesh.rotation.x = 0.12 + py * 0.06;
    const show = 1 - Math.min(1, k * 3); pinMat.opacity = show;
    pinObjs.forEach((o, i) => { const ph = (t * 0.8 + i * 0.37) % 1; o.ring.scale.setScalar(1 + ph * 2.2); (o.ring.material as THREE.MeshBasicMaterial).opacity = show * (1 - ph); });
  }, dispose() {} };
}

/* h4 洋流粒子 */
function h4({ scene, camera, isDark, palette }: Ctx): Built {
  camera.position.set(0, 0.6, 10.8); camera.lookAt(0, 0, 0);
  const R = 2.45, c = pal(isDark), root = new THREE.Group(); root.rotation.z = 0.35; scene.add(root);
  const body = new THREE.ShaderMaterial({ uniforms: { uM: { value: maskTex() }, uSea: { value: isDark ? new THREE.Color("#3a4f5e") : new THREE.Color("#e6eef3") }, uLand: { value: isDark ? new THREE.Color("#3f6a57") : new THREE.Color("#c9d8c4") } },
    vertexShader: `varying vec2 vUv; varying vec3 vN; varying vec3 vV; void main(){ vUv=uv; vec4 mv=modelViewMatrix*vec4(position,1.); vN=normalize(normalMatrix*normal); vV=normalize(-mv.xyz); gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `uniform sampler2D uM; uniform vec3 uSea; uniform vec3 uLand; varying vec2 vUv; varying vec3 vN; varying vec3 vV; void main(){ float l=smoothstep(.4,.6,texture2D(uM,vUv).r); vec3 col=mix(uSea,uLand,l); col*=.8+.2*dot(vN,vV); gl_FragColor=vec4(col,1.); }` });
  root.add(new THREE.Mesh(new THREE.SphereGeometry(R, 96, 64), body));
  scene.add(atmosphere(R, c.rim, isDark ? 0.6 : 0.6, isDark));
  // 粒子：每个粒子带一条短尾（TRAIL 个点），沿纬向流场前进
  const N = 1100, TR = 8, P = new Float32Array(N * TR * 3), C = new Float32Array(N * TR * 3), A = new Float32Array(N * TR);
  const lon = new Float32Array(N), lat = new Float32Array(N), sp = new Float32Array(N), life = new Float32Array(N);
  const cols = isDark ? [new THREE.Color("#bfe3d3"), new THREE.Color("#8fc3dc")] : [new THREE.Color("#3e6f8a"), new THREE.Color("#4f7d5b")];
  for (let i = 0; i < N; i++) { lon[i] = Math.random() * Math.PI * 2; lat[i] = Math.asin(Math.random() * 2 - 1) * 0.95; sp[i] = 0.5 + Math.random() * 0.6; life[i] = Math.random(); const cc = cols[i % 2]; for (let k = 0; k < TR; k++) { cc.toArray(C, (i * TR + k) * 3); A[i * TR + k] = (1 - k / TR); } }
  const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.BufferAttribute(P, 3)); g.setAttribute("color", new THREE.BufferAttribute(C, 3)); g.setAttribute("alpha", new THREE.BufferAttribute(A, 1));
  const lifeA = new Float32Array(N * TR); g.setAttribute("life", new THREE.BufferAttribute(lifeA, 1));
  const pm = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, blending: isDark ? THREE.AdditiveBlending : THREE.NormalBlending, uniforms: { uPx: { value: 42 * DPR() }, uK: { value: isDark ? 0.55 : 0.85 } },
    vertexShader: `attribute vec3 color; attribute float alpha; attribute float life; uniform float uPx; varying vec3 vC; varying float vA; void main(){ vC=color; vec4 mv=modelViewMatrix*vec4(position,1.); vec3 n=normalize((modelViewMatrix*vec4(position,0.)).xyz); float face=smoothstep(-.1,.3,n.z);
      vA=alpha*face*sin(life*3.14159); gl_PointSize=uPx*(.6+alpha*.6)/(-mv.z); gl_Position=projectionMatrix*mv; }`,
    fragmentShader: `uniform float uK; varying vec3 vC; varying float vA; void main(){ float d=length(gl_PointCoord-.5); float a=smoothstep(.5,.1,d)*vA*uK; if(a<.01) discard; gl_FragColor=vec4(vC,a); }` });
  root.add(new THREE.Points(g, pm));
  // 流场：信风 / 西风带 / 极地东风三带 + 缓慢的经向蜿蜒
  const vel = (la: number, lo: number, t: number) => { const b = Math.abs(la); const u = b < 0.52 ? -0.35 : b < 1.05 ? 0.55 : -0.25; return [u + 0.15 * Math.sin(lo * 3 + t * 0.4), 0.12 * Math.sin(lo * 2 + la * 3 + t * 0.3)]; };
  const R2 = R * 1.012;
  return { update(t, px, py) {
    root.rotation.y = -1.9 + t * 0.08 + px * 0.25; root.rotation.x = py * 0.08;
    for (let i = 0; i < N; i++) {
      life[i] += 0.006 * sp[i]; if (life[i] > 1) { life[i] = 0; lon[i] = Math.random() * Math.PI * 2; lat[i] = Math.asin(Math.random() * 2 - 1) * 0.95; }
      let la = lat[i], lo = lon[i];
      for (let k = 0; k < TR; k++) {
        const [u, v] = vel(la, lo, t); const j = (i * TR + k) * 3; const cl = Math.cos(la);
        P[j] = cl * Math.cos(lo) * R2; P[j + 1] = Math.sin(la) * R2; P[j + 2] = cl * Math.sin(lo) * R2; lifeA[i * TR + k] = life[i];
        lo -= u * 0.03 * sp[i] / Math.max(0.3, cl); la -= v * 0.03 * sp[i];
        if (k === 0) { const [u0, v0] = [u, v]; lon[i] += u0 * 0.008 * sp[i] / Math.max(0.3, cl); lat[i] = Math.max(-1.45, Math.min(1.45, lat[i] + v0 * 0.008 * sp[i])); }
      }
    }
    g.attributes.position.needsUpdate = true; g.attributes.life.needsUpdate = true;
  }, dispose() {} };
}

export function buildGlobeVariant2(v: GlobeVariant2, ctx: Ctx): Built { return ({ h1, h2, h3, h4 } as const)[v](ctx); }
