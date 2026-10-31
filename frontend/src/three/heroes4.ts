// k4「制度文件层叠」的变体（?globe=m1..m4），细化版：纸张纹理、烘焙柔和投影、编号条款行、实心勾选，统一缓动。
// m1 环形轮播：七张卡围成转盘，停驻时正面卡被扫描核对，再平滑转到下一张；
// m2 叠卡翻页：一摞卡，顶卡扫描打勾后向后翻起压到底部，循环；
// m3 审核流水线：卡片沿传送带穿过扫描闸门，过闸逐行变绿，两端淡入淡出；
// m4 条款分层：整卡沿纵深拆成逐条条款，扫描光逐条穿过并打勾，再合拢。
import * as THREE from "three";

export type HeroVariant4 = "m1" | "m2" | "m3" | "m4";
type Ctx = { scene: THREE.Scene; camera: THREE.PerspectiveCamera; isDark: boolean; palette: Record<"a" | "b" | "c" | "d", THREE.Color> };
type Built = { update: (t: number, px: number, py: number) => void; dispose: () => void };
const pal = (d: boolean) => d
  ? { line: "#8fa9ba", text: "#c9d6dd", ok: "#5fae8a", warn: "#d2a24c", card: "#36434d", card2: "#2f3b44", shadow: "rgba(0,0,0,.55)" }
  : { line: "#5c7f99", text: "#46617a", ok: "#5f9466", warn: "#c9963f", card: "#fdfcf8", card2: "#f2efe6", shadow: "rgba(40,60,80,.28)" };
type P = ReturnType<typeof pal>;
const ROWS = 9, CW = 2.4, CH = 3.2, PAD = .14; // PAD：贴图四周留给投影的边距（世界单位）
const ease = (x: number) => { x = Math.min(1, Math.max(0, x)); return x < .5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2; };
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

function rr(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) { g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r); g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath(); }
// 卡片贴图：checked=已核对行数；shadow=是否烘焙投影；crop 用于条款分层的单行贴图
function cardTex(c: P, checked: number, seed = 0, shadow = true) {
  const S = 2, W = 240 * S, H = 320 * S, pad = shadow ? Math.round(PAD / CW * W) : 0;
  const cv = document.createElement("canvas"); cv.width = W + pad * 2; cv.height = H + pad * 2; const g = cv.getContext("2d")!; g.translate(pad, pad);
  if (shadow) { g.save(); g.shadowColor = c.shadow; g.shadowBlur = 22 * S; g.shadowOffsetY = 6 * S; rr(g, 0, 0, W, H, 8 * S); g.fillStyle = c.card; g.fill(); g.restore(); }
  rr(g, 0, 0, W, H, 8 * S); g.save(); g.clip();
  const grd = g.createLinearGradient(0, 0, W, H); grd.addColorStop(0, c.card); grd.addColorStop(1, c.card2); g.fillStyle = grd; g.fillRect(0, 0, W, H);
  let s = 11 + seed * 97; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647; // 纸纹噪点
  for (let i = 0; i < 2600; i++) { g.fillStyle = rnd() > .5 ? "rgba(255,255,255,.05)" : "rgba(0,0,0,.035)"; g.fillRect(rnd() * W, rnd() * H, S, S); }
  g.fillStyle = c.line; g.globalAlpha = .9; g.fillRect(0, 0, 5 * S, H); g.globalAlpha = 1; // 左侧装订色条
  g.fillStyle = c.text; g.font = `600 ${15 * S}px "PingFang SC","Noto Sans CJK SC",sans-serif`; g.fillText(["信息安全管理制度", "数据分类分级规范", "供应商风险管理办法", "访问控制策略"][seed % 4], 22 * S, 34 * S);
  g.font = `${8.5 * S}px sans-serif`; g.globalAlpha = .6; g.fillText(`POL-${String(101 + seed * 7)} · v${2 + (seed % 3)}.${seed % 4}`, 22 * S, 49 * S); g.globalAlpha = 1;
  g.fillStyle = seed % 3 === 1 ? c.warn : c.ok; rr(g, 182 * S, 22 * S, 38 * S, 15 * S, 7 * S); g.fill(); g.fillStyle = "#fff"; g.font = `600 ${8 * S}px sans-serif`; g.fillText(seed % 3 === 1 ? "复核" : "生效", 191 * S, 32.5 * S);
  g.strokeStyle = c.line; g.globalAlpha = .25; g.lineWidth = S; g.beginPath(); g.moveTo(22 * S, 58 * S); g.lineTo(220 * S, 58 * S); g.stroke(); g.globalAlpha = 1;
  for (let r = 0; r < ROWS; r++) { const y = (70 + r * 26) * S, done = r < checked;
    g.fillStyle = c.text; g.globalAlpha = .55; g.font = `600 ${8 * S}px sans-serif`; g.fillText(`${r + 1}.`, 22 * S, y + 8 * S);
    g.globalAlpha = done ? .55 : .35; g.fillStyle = c.line; rr(g, 36 * S, y + 1.5 * S, (110 + ((r * 37 + seed * 13) % 46)) * S, 4.5 * S, 2 * S); g.fill(); rr(g, 36 * S, y + 10 * S, (60 + ((r * 53 + seed * 7) % 50)) * S, 3.5 * S, 2 * S); g.globalAlpha *= .7; g.fill(); g.globalAlpha = 1;
    rr(g, 198 * S, y - 1 * S, 15 * S, 15 * S, 3.5 * S);
    if (done) { g.fillStyle = c.ok; g.fill(); g.strokeStyle = "#fff"; g.lineWidth = 2.2 * S; g.lineCap = "round"; g.lineJoin = "round"; g.beginPath(); g.moveTo(201.5 * S, y + 6.5 * S); g.lineTo(205 * S, y + 10 * S); g.lineTo(210 * S, y + 3 * S); g.stroke(); }
    else { g.strokeStyle = c.ok; g.globalAlpha = .7; g.lineWidth = 1.6 * S; g.stroke(); g.globalAlpha = 1; } }
  g.restore();
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; return t;
}
const texSet = (c: P, seed = 0, shadow = true) => Array.from({ length: ROWS + 1 }, (_, k) => cardTex(c, k, seed, shadow));
const pg = (k = 1) => new THREE.PlaneGeometry((CW + PAD * 2) * k, (CH + PAD * 2 * CH / CW) * k);
const mat = (map: THREE.Texture, op = 1) => new THREE.MeshBasicMaterial({ map, transparent: true, opacity: op, side: THREE.DoubleSide, depthWrite: false });
// 扫描线：中心亮、两端渐隐 + 下方柔光带
function scanBar(c: P, w = CW) {
  const cv = document.createElement("canvas"); cv.width = 256; cv.height = 32; const g = cv.getContext("2d")!;
  const gx = g.createLinearGradient(0, 0, 256, 0); gx.addColorStop(0, "rgba(255,255,255,0)"); gx.addColorStop(.15, "#fff"); gx.addColorStop(.85, "#fff"); gx.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = gx; g.fillRect(0, 14, 256, 3); g.globalAlpha = .22; g.fillRect(0, 0, 256, 14); g.globalAlpha = 1;
  const t = new THREE.CanvasTexture(cv);
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w + .2, .26), new THREE.MeshBasicMaterial({ map: t, color: c.ok, transparent: true, depthWrite: false, side: THREE.DoubleSide })); m.userData.tex = t; return m;
}
const rowsDone = (s: number) => Math.max(0, Math.min(ROWS, Math.floor(s * ROWS + .15)));
const scanY = (s: number) => CH / 2 - (64 + s * ROWS * 26) / 320 * CH;
const place = (bar: THREE.Object3D, card: THREE.Object3D, y: number) => { bar.position.copy(card.position); bar.quaternion.copy(card.quaternion); bar.translateY(y); bar.translateZ(.015); };
function cleanup(root: THREE.Object3D, texs: THREE.Texture[]) { root.traverse((m: any) => { m.geometry?.dispose?.(); m.material?.dispose?.(); m.userData?.tex?.dispose?.(); }); texs.forEach(t => t.dispose()); root.removeFromParent(); }

/* m1 环形轮播 */
function m1({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, 1.4, 12.5); camera.lookAt(0, -.1, 0);
  const c = pal(isDark), root = new THREE.Group(); scene.add(root);
  const N = 7, sets = Array.from({ length: N }, (_, i) => texSet(c, i)), T = sets.flat(), Rr = 4.3;
  const cards = Array.from({ length: N }, (_, i) => { const m = new THREE.Mesh(pg(), mat(sets[i][0])); root.add(m); return m; });
  const bar = scanBar(c); root.add(bar);
  const per = 3.6, scanF = .55;
  return { update(t, px, py) {
    root.rotation.x = .05 + py * .06; const step = (Math.PI * 2) / N, k = t / per, idx = Math.floor(k), f = k - idx;
    const rot = (idx + ease((f - scanF) / (1 - scanF))) * step + px * .15, s = ease(f / scanF * 1.05);
    cards.forEach((m, i) => { const a = i * step - rot; m.position.set(Math.sin(a) * Rr, 0, Math.cos(a) * Rr - Rr); m.rotation.y = a; m.renderOrder = Math.round(m.position.z * 10) + 100;
      const ord = ((i - idx) % N + N) % N, mm = m.material as THREE.MeshBasicMaterial;
      mm.map = sets[i][ord === 0 ? rowsDone(s) : ord >= N - 1 ? ROWS : 0]; mm.opacity = .25 + .75 * Math.max(0, Math.cos(a)) ** 1.5; });
    const fc = cards[((idx % N) + N) % N]; bar.visible = f < scanF; place(bar, fc, scanY(s)); bar.renderOrder = 999;
    (bar.material as THREE.MeshBasicMaterial).opacity = Math.min(1, Math.sin(clamp01(f / scanF) * Math.PI) * 3);
  }, dispose() { cleanup(root, T); } };
}

/* m2 叠卡翻页 */
function m2({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, 2.2, 10); camera.lookAt(0, -.1, 0);
  const c = pal(isDark), root = new THREE.Group(); root.rotation.x = -.38; scene.add(root);
  const N = 6, sets = Array.from({ length: N }, (_, i) => texSet(c, i)), T = sets.flat();
  const cards = Array.from({ length: N }, (_, i) => { const m = new THREE.Mesh(pg(), mat(sets[i][0])); root.add(m); return m; });
  const bar = scanBar(c); root.add(bar);
  const per = 3.8, scanF = .62, dz = .12;
  return { update(t, px, py) {
    root.rotation.z = Math.sin(t * .3) * .03 + px * .06; root.rotation.y = Math.sin(t * .22) * .08 + py * .06;
    const k = t / per, idx = Math.floor(k), f = k - idx, s = ease(f / scanF * 1.05), e = ease((f - scanF) / (1 - scanF));
    cards.forEach((m, i) => { const ord = ((i - idx) % N + N) % N, mm = m.material as THREE.MeshBasicMaterial;
      const settle = ease(e * 1.2); // 其余卡片在翻页时一起前移一格
      if (ord === 0) { mm.map = sets[i][rowsDone(s)];
        // 以卡片上沿为轴向后翻起，再沉到最底层
        const ang = e * Math.PI, lift = Math.sin(e * Math.PI);
        m.position.set(0, CH * .5 - Math.cos(ang) * CH * .5 + lift * .25, N * dz - e * N * dz + Math.sin(ang) * CH * .5 * .6); m.rotation.set(-ang, 0, 0);
        m.renderOrder = e > .5 ? 1 : 200; mm.opacity = 1 - lift * .25;
        bar.visible = f < scanF; place(bar, m, scanY(s)); bar.renderOrder = 999; (bar.material as THREE.MeshBasicMaterial).opacity = Math.min(1, Math.sin(clamp01(f / scanF) * Math.PI) * 3); }
      else { const o = ord - settle; m.position.set(o * .05, -o * .06, (N - o) * dz); m.rotation.set(0, 0, (i % 2 ? .025 : -.025) * Math.min(1, o)); m.renderOrder = 200 - ord; mm.map = sets[i][0]; mm.opacity = 1 - o * .08; } });
  }, dispose() { cleanup(root, T); } };
}

/* m3 审核流水线 */
function m3({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, .6, 14); camera.lookAt(0, -.1, 0);
  const c = pal(isDark), root = new THREE.Group(); scene.add(root);
  const N = 6, gap = 1.85, K = .66, span = N * gap, sets = Array.from({ length: 3 }, (_, i) => texSet(c, i)), T = sets.flat();
  const cards = Array.from({ length: N }, () => { const m = new THREE.Mesh(pg(K), mat(sets[0][0])); root.add(m); return m; });
  const gate = new THREE.Group(); root.add(gate);
  const gm = new THREE.MeshStandardMaterial({ color: c.line, roughness: .4, metalness: .2 });
  const gh = CH * K + .5, gw = CW * K + .45;
  [-1, 1].forEach(s => { const p = new THREE.Mesh(new THREE.BoxGeometry(.09, gh, .14), gm); p.position.x = s * gw / 2; gate.add(p); });
  const top = new THREE.Mesh(new THREE.BoxGeometry(gw + .09, .09, .14), gm); top.position.y = gh / 2; gate.add(top);
  const lamp = new THREE.Mesh(new THREE.SphereGeometry(.07, 16, 12), new THREE.MeshBasicMaterial({ color: c.ok })); lamp.position.set(0, gh / 2 + .1, .05); gate.add(lamp);
  const beamBar = scanBar(c, gw - .1); beamBar.rotation.z = Math.PI / 2; beamBar.scale.set(gh / (gw + .1), 1, 1); beamBar.position.z = .02; gate.add(beamBar);
  const L1 = new THREE.HemisphereLight(0xffffff, 0x99aaaa, 2.2); root.add(L1);
  const belt = new THREE.Mesh(new THREE.PlaneGeometry(span * .85, .9), new THREE.MeshBasicMaterial({ color: c.line, transparent: true, opacity: isDark ? .16 : .1, side: THREE.DoubleSide, depthWrite: false })); belt.rotation.x = -Math.PI / 2; belt.position.y = -gh / 2; root.add(belt);
  const ticksG = new THREE.Group(); root.add(ticksG);
  const marks = Array.from({ length: 18 }, (_, i) => { const m = new THREE.Mesh(new THREE.PlaneGeometry(.06, .5), new THREE.MeshBasicMaterial({ color: c.line, transparent: true, opacity: .25, depthWrite: false })); m.rotation.x = -Math.PI / 2; m.position.y = -gh / 2 + .005; m.userData.i = i; ticksG.add(m); return m; });
  return { update(t, px, py) {
    root.rotation.y = .22 + px * .12; root.rotation.x = .1 + py * .05;
    const v = .55, sh = t * v; (lamp.material as THREE.MeshBasicMaterial).color.set(c.ok); lamp.scale.setScalar(1 + .25 * Math.sin(t * 5));
    marks.forEach(m => { m.position.x = ((m.userData.i * span / 18 - sh) % span + span) % span - span / 2; (m.material as THREE.MeshBasicMaterial).opacity = .25 * clamp01((span * .42 - Math.abs(m.position.x)) / 1.2); });
    let near = 0;
    cards.forEach((m, i) => { const x = ((i * gap - sh) % span + span) % span - span / 2; m.position.set(x, 0, 0);
      const s = clamp01((gw / 2 - x) / gw); (m.material as THREE.MeshBasicMaterial).map = sets[i % 3][rowsDone(ease(s))];
      (m.material as THREE.MeshBasicMaterial).opacity = ease(clamp01((span / 2 - Math.abs(x)) / 1.8)); m.renderOrder = 100 + i; if (Math.abs(x) < gw / 2) near = 1 - Math.abs(x) / (gw / 2); });
    (beamBar.material as THREE.MeshBasicMaterial).opacity = .35 + .65 * near;
  }, dispose() { cleanup(root, T); } };
}

/* m4 条款分层 */
function m4({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, 0, 10.5); camera.lookAt(0, 0, 0);
  const c = pal(isDark), root = new THREE.Group(); scene.add(root);
  const T = texSet(c, 2, false); // 无投影边距，方便按行切 uv
  const head = 64 / 320, rowH = 26 / 320; // 头部与每行占卡片高度的比例
  const bands: [number, number][] = [[0, head], ...Array.from({ length: ROWS }, (_, r) => [head + r * rowH, head + (r + 1) * rowH] as [number, number]), [head + ROWS * rowH, 1]];
  const shTex = (() => { const cv = document.createElement("canvas"); cv.width = 64; cv.height = 16; const g = cv.getContext("2d")!; const gr = g.createRadialGradient(32, 8, 1, 32, 8, 30); gr.addColorStop(0, c.shadow); gr.addColorStop(1, "rgba(0,0,0,0)"); g.fillStyle = gr; g.fillRect(0, 0, 64, 16); return new THREE.CanvasTexture(cv); })();
  const strips = bands.map(([a, b], r) => { const h = (b - a) * CH, g = new THREE.PlaneGeometry(CW, h); const uv = g.attributes.uv as THREE.BufferAttribute; for (let k = 0; k < uv.count; k++) uv.setY(k, 1 - (uv.getY(k) > .5 ? a : b));
    const m = new THREE.Mesh(g, mat(T[0])); m.userData = { r, y0: CH / 2 - (a + b) / 2 * CH }; root.add(m);
    const sd = new THREE.Mesh(new THREE.PlaneGeometry(CW * 1.05, h * 1.2 + .1), new THREE.MeshBasicMaterial({ map: shTex, transparent: true, depthWrite: false, opacity: 0 })); root.add(sd); m.userData.sd = sd; return m; });
  const bar = scanBar(c); root.add(bar);
  const per = 7.5;
  return { update(t, px, py) {
    root.rotation.y = -.5 + Math.sin(t * .25) * .08 + px * .15; root.rotation.x = .1 + py * .06;
    const f = (t / per) % 1, e = ease(f / .18) * (1 - ease((f - .8) / .18)), s = clamp01((f - .22) / .5);
    const cur = s * (ROWS + 1) - .5; // 当前扫描到的条款（浮点）
    strips.forEach(m => { const { r, y0, sd } = m.userData as { r: number; y0: number; sd: THREE.Mesh };
      m.position.set(Math.sin(r * 1.3 + t * .8) * .06 * e, y0 * (1 + .32 * e), (r - (ROWS + 1) / 2) * -.34 * e + Math.sin(t * 1.1 + r) * .03 * e);
      const near = Math.max(0, 1 - Math.abs(r - cur) * 1.2) * (f > .22 && f < .72 ? 1 : 0); m.position.z += near * .25 * e;
      (m.material as THREE.MeshBasicMaterial).map = T[f < .22 ? 0 : rowsDone(s)]; m.renderOrder = 10 + r * 2;
      sd.position.set(m.position.x + .06, m.position.y - .08, m.position.z - .03); (sd.material as THREE.MeshBasicMaterial).opacity = e * .9; sd.renderOrder = 9 + r * 2; });
    const ri = Math.max(1, Math.min(ROWS, Math.round(cur))), sm = strips[ri];
    bar.visible = f > .22 && f < .72; bar.position.copy(sm.position); bar.translateZ(.02); bar.position.y = sm.position.y + (cur - Math.round(cur)) * -rowH * CH * 1.3; bar.renderOrder = 999;
    (bar.material as THREE.MeshBasicMaterial).opacity = Math.min(1, Math.sin(clamp01((f - .22) / .5) * Math.PI) * 3);
  }, dispose() { cleanup(root, [...T, shTex]); } };
}

export function buildHeroVariant4(v: HeroVariant4, ctx: Ctx): Built { return ({ m1, m2, m3, m4 } as const)[v](ctx); }
