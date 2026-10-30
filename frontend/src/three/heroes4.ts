// k4「制度文件层叠」的变体（?globe=m1..m4）：
// m1 环形轮播：卡片围成一圈竖直转盘缓转，转到正面的那张被扫描核对；
// m2 叠卡翻页：一摞制度卡，顶上一张扫描完打勾后翻到底部，循环；
// m3 审核流水线：卡片沿斜向传送带穿过扫描闸门，过闸后勾选框依次变绿；
// m4 条款分层：一张制度卡沿纵深拆成逐条条款悬浮，扫描光穿过后再合拢。
import * as THREE from "three";

export type HeroVariant4 = "m1" | "m2" | "m3" | "m4";
type Ctx = { scene: THREE.Scene; camera: THREE.PerspectiveCamera; isDark: boolean; palette: Record<"a" | "b" | "c" | "d", THREE.Color> };
type Built = { update: (t: number, px: number, py: number) => void; dispose: () => void };
const pal = (d: boolean) => d
  ? { line: "#6f8ea3", ok: "#5fae8a", warn: "#d2a24c", card: "#34414b", bg: "#2a333b" }
  : { line: "#5c7f99", ok: "#6f9a6c", warn: "#d1a04a", card: "#ffffff", bg: "#e6ecef" };
type P = ReturnType<typeof pal>;
const ROWS = 10, CW = 2.4, CH = 3.2;
// 卡片贴图；checked=已核对行数（勾选框填充）
function cardTex(c: P, checked: number, seed = 0) {
  const cv = document.createElement("canvas"); cv.width = 240; cv.height = 320; const g = cv.getContext("2d")!;
  g.fillStyle = c.card; g.fillRect(0, 0, 240, 320); g.strokeStyle = c.line; g.globalAlpha = .35; g.strokeRect(1, 1, 238, 318); g.globalAlpha = 1;
  g.fillStyle = c.line; g.fillRect(22, 22, 120, 14); g.fillStyle = seed % 3 === 1 ? c.warn : c.ok; g.fillRect(190, 20, 28, 18);
  for (let r = 0; r < ROWS; r++) { const y = 58 + r * 25; g.fillStyle = c.line; g.globalAlpha = .45; g.fillRect(22, y + 3, 120 + ((r * 37 + seed * 13) % 40), 7); g.globalAlpha = 1;
    g.strokeStyle = c.ok; g.lineWidth = 2.5; g.strokeRect(196, y, 14, 14);
    if (r < checked) { g.beginPath(); g.moveTo(199, y + 7); g.lineTo(203, y + 11); g.lineTo(209, y + 3); g.stroke(); } }
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; return t;
}
// 0..ROWS 每种核对进度一张贴图，按需切换
function texSet(c: P, seed = 0) { return Array.from({ length: ROWS + 1 }, (_, k) => cardTex(c, k, seed)); }
const pg = () => new THREE.PlaneGeometry(CW, CH);
const mat = (map: THREE.Texture, op = .93) => new THREE.MeshBasicMaterial({ map, transparent: true, opacity: op, side: THREE.DoubleSide, depthWrite: false });
const scanBar = (c: P, w = CW + .1) => new THREE.Mesh(new THREE.PlaneGeometry(w, .05), new THREE.MeshBasicMaterial({ color: c.ok, transparent: true, opacity: .9, depthWrite: false }));
const rowsDone = (s: number) => Math.max(0, Math.min(ROWS, Math.floor(s * (ROWS + 1)))); // s:0..1 扫描进度
const scanY = (s: number) => CH / 2 - .1 - s * (CH - .2);
function cleanup(root: THREE.Object3D, texs: THREE.Texture[]) { root.traverse((m: any) => { m.geometry?.dispose?.(); m.material?.dispose?.(); }); texs.forEach(t => t.dispose()); root.removeFromParent(); }

/* m1 环形轮播 */
function m1({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, 1.2, 11); camera.lookAt(0, 0, 0);
  const c = pal(isDark), root = new THREE.Group(); scene.add(root); const T = texSet(c);
  const N = 8, Rr = 3.6, cards = Array.from({ length: N }, () => { const m = new THREE.Mesh(pg(), mat(T[0])); root.add(m); return m; });
  const bar = scanBar(c); root.add(bar);
  return { update(t, px, py) {
    root.rotation.x = .06 + py * .08; const step = (Math.PI * 2) / N, per = 3, k = t / per, idx = Math.floor(k), f = k - idx;
    const ease = f < .35 ? 0 : (1 - Math.cos(((f - .35) / .65) * Math.PI)) / 2; const rot = (idx + ease) * step + px * .2;
    cards.forEach((m, i) => { const a = i * step - rot; m.position.set(Math.sin(a) * Rr, 0, Math.cos(a) * Rr - Rr * .55); m.rotation.y = a; m.renderOrder = Math.round(m.position.z * 10) + 50;
      const front = ((i - idx) % N + N) % N === 0; const s = front ? Math.min(1, f / .35) : 0; (m.material as THREE.MeshBasicMaterial).map = T[front ? rowsDone(s) : (((i - idx) % N + N) % N > N / 2 ? ROWS : 0)];
      (m.material as THREE.MeshBasicMaterial).opacity = .35 + .6 * Math.max(0, Math.cos(a)); });
    const fc = cards[((idx % N) + N) % N]; const s = Math.min(1, f / .35); bar.visible = f < .35; bar.position.copy(fc.position); bar.rotation.copy(fc.rotation); bar.translateY(scanY(s)); bar.translateZ(.02); bar.renderOrder = 999;
  }, dispose() { cleanup(root, T); } };
}

/* m2 叠卡翻页 */
function m2({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, 2.6, 7.8); camera.lookAt(0, -.2, 0);
  const c = pal(isDark), root = new THREE.Group(); root.rotation.x = -.5; scene.add(root);
  const N = 6, sets = Array.from({ length: N }, (_, i) => texSet(c, i)); const T = sets.flat();
  const cards = Array.from({ length: N }, (_, i) => { const m = new THREE.Mesh(pg(), mat(sets[i][0], .96)); root.add(m); return m; });
  const bar = scanBar(c); root.add(bar);
  return { update(t, px, py) {
    root.rotation.z = Math.sin(t * .3) * .05 + px * .1; root.rotation.y = py * .1;
    const per = 3.2, k = t / per, idx = Math.floor(k), f = k - idx;
    cards.forEach((m, i) => { const order = ((i - idx) % N + N) % N; const mm = m.material as THREE.MeshBasicMaterial;
      if (order === 0) { const s = Math.min(1, f / .6); mm.map = sets[i][rowsDone(s)];
        const fl = f < .6 ? 0 : (f - .6) / .4, e = fl * fl * (3 - 2 * fl);
        m.position.set(Math.sin(e * Math.PI) * 2.2, 0, N * .14 + Math.sin(e * Math.PI) * 1.4 - e * N * .14); m.rotation.set(0, -e * Math.PI, e * .15); m.renderOrder = e > .5 ? 0 : 100;
        bar.visible = f < .6; bar.position.copy(m.position); bar.rotation.copy(m.rotation); bar.translateY(scanY(s)); bar.translateZ(.02); bar.renderOrder = 999; }
      else { const z = (N - order) * .14 + (order === N - 1 ? 0 : 0); m.position.set(order * .06, -order * .05, z); m.rotation.set(0, 0, (order % 2 ? .03 : -.03)); m.renderOrder = 100 - order; mm.map = sets[i][0]; }
      mm.opacity = order === 0 ? .97 : .97 - order * .1; });
  }, dispose() { cleanup(root, T); } };
}

/* m3 审核流水线 */
function m3({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, 0, 12); camera.lookAt(0, 0, 0);
  const c = pal(isDark), root = new THREE.Group(); root.rotation.set(-.15, .35, 0); scene.add(root);
  const N = 7, gap = 2.2, sets = Array.from({ length: 3 }, (_, i) => texSet(c, i)); const T = sets.flat();
  const cards = Array.from({ length: N }, () => { const m = new THREE.Mesh(new THREE.PlaneGeometry(CW * .8, CH * .8), mat(sets[0][0])); root.add(m); return m; });
  const gate = new THREE.Group(); root.add(gate);
  const gm = new THREE.MeshBasicMaterial({ color: c.line, transparent: true, opacity: .7 });
  [-1, 1].forEach(s => { const p = new THREE.Mesh(new THREE.BoxGeometry(.08, CH * .95, .08), gm); p.position.x = s * CW * .5; gate.add(p); });
  const top = new THREE.Mesh(new THREE.BoxGeometry(CW + .08, .08, .08), gm); top.position.y = CH * .47; gate.add(top);
  const beam = new THREE.Mesh(new THREE.PlaneGeometry(CW, CH * .9), new THREE.MeshBasicMaterial({ color: c.ok, transparent: true, opacity: .18, side: THREE.DoubleSide, depthWrite: false })); gate.add(beam);
  gate.position.z = .05;
  const belt = new THREE.Mesh(new THREE.PlaneGeometry(N * gap * .8, 1.2), new THREE.MeshBasicMaterial({ color: c.line, transparent: true, opacity: .12, side: THREE.DoubleSide })); belt.rotation.x = -Math.PI / 2; belt.position.y = -CH * .42; root.add(belt);
  return { update(t, px, py) {
    root.rotation.y = .35 + px * .15; root.rotation.x = -.15 + py * .08;
    (beam.material as THREE.MeshBasicMaterial).opacity = .14 + .1 * Math.sin(t * 6);
    cards.forEach((m, i) => { let x = ((i * gap - t * .7) % (N * gap) + N * gap) % (N * gap) - N * gap / 2; m.position.set(x, 0, 0); m.rotation.y = 0; m.position.z = -Math.abs(x) * .25;
      const s = Math.min(1, Math.max(0, (-x + .6) / 1.6)); (m.material as THREE.MeshBasicMaterial).map = sets[i % 3][rowsDone(s)];
      (m.material as THREE.MeshBasicMaterial).opacity = Math.min(1, (N * gap / 2 - Math.abs(x)) / 1.5) * .93; m.renderOrder = Math.round(-x * 10) + 100; });
  }, dispose() { cleanup(root, T); } };
}

/* m4 条款分层 */
function m4({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, 0, 10); camera.lookAt(0, 0, 0);
  const c = pal(isDark), root = new THREE.Group(); scene.add(root);
  const T = texSet(c, 2), full = T[ROWS];
  const strips: THREE.Mesh[] = []; const H = CH / (ROWS + 2);
  for (let r = 0; r < ROWS + 2; r++) { const g = new THREE.PlaneGeometry(CW, H); const uv = g.attributes.uv as THREE.BufferAttribute; for (let k = 0; k < uv.count; k++) uv.setY(k, 1 - (r + 1 - uv.getY(k)) / (ROWS + 2)); const m = new THREE.Mesh(g, mat(T[0], .95)); m.userData.r = r; root.add(m); strips.push(m); }
  const beam = new THREE.Mesh(new THREE.PlaneGeometry(.06, CH * 1.3), new THREE.MeshBasicMaterial({ color: c.ok, transparent: true, opacity: .9, depthWrite: false })); root.add(beam);
  return { update(t, px, py) {
    root.rotation.y = -.55 + Math.sin(t * .3) * .12 + px * .2; root.rotation.x = .12 + py * .1;
    const per = 6, f = (t / per) % 1; const ex = f < .2 ? f / .2 : f < .75 ? 1 : 1 - (f - .75) / .25; const e = ex * ex * (3 - 2 * ex);
    const s = Math.min(1, Math.max(0, (f - .25) / .45)); const done = f >= .25 && f < .97;
    strips.forEach(m => { const r = m.userData.r as number; const y = CH / 2 - H * (r + .5);
      m.position.set(Math.sin(r * 1.3 + t) * .15 * e, y + (y * .35) * e, (r - ROWS / 2) * -.32 * e + Math.sin(t * 1.2 + r) * .05 * e);
      (m.material as THREE.MeshBasicMaterial).map = done ? T[Math.min(ROWS, rowsDone(s))] : (f >= .97 ? full : T[0]); m.renderOrder = r + 10; });
    beam.visible = f > .25 && f < .7; beam.position.set(0, 0, CH * .5 - s * CH * 1.4 + 1.6); beam.rotation.y = Math.PI / 2; beam.position.x = 0; beam.renderOrder = 999;
    beam.position.set(0, (CH / 2 - s * CH) * 1.35, .4);  beam.rotation.set(0, 0, Math.PI / 2); (beam.geometry as THREE.PlaneGeometry);
  }, dispose() { cleanup(root, T); } };
}

export function buildHeroVariant4(v: HeroVariant4, ctx: Ctx): Built { return ({ m1, m2, m3, m4 } as const)[v](ctx); }
