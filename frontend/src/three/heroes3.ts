// 第三批登录页主视觉（?globe=k1..k5），不再是地球：
// k1 控制网络：控制点组成的立体网络，信号脉冲沿连线流动，被“检查”的节点亮起；
// k2 风险热力矩阵：可能性×影响 8×8 立体柱阵，高度与颜色像潮汐一样起伏；
// k3 审计链：一节节记录方块向左平移，新方块从上方落下并扣上链；
// k4 制度文件层叠：半透明制度卡片扇形展开、轮换，扫描线逐行核对；
// k5 三道防线：三层盾形护罩缓转，外来风险粒子被逐层拦截并消散。
import * as THREE from "three";

export type HeroVariant3 = "k1" | "k2" | "k3" | "k4" | "k5";
type Ctx = { scene: THREE.Scene; camera: THREE.PerspectiveCamera; isDark: boolean; palette: Record<"a" | "b" | "c" | "d", THREE.Color> };
type Built = { update: (t: number, px: number, py: number) => void; dispose: () => void };
const C = (s: string) => new THREE.Color(s);
const pal = (d: boolean) => d
  ? { base: C("#4a5b68"), line: C("#6f8ea3"), ok: C("#5fae8a"), warn: C("#d2a24c"), risk: C("#c9735a"), ink: C("#d6e4dd"), card: C("#34414b") }
  : { base: C("#b9c9d4"), line: C("#5c7f99"), ok: C("#6f9a6c"), warn: C("#d1a04a"), risk: C("#c26b52"), ink: C("#3e5a6e"), card: C("#ffffff") };
function lights(scene: THREE.Scene, isDark: boolean) {
  const g = new THREE.Group();
  g.add(new THREE.HemisphereLight(0xffffff, isDark ? 0x223030 : 0xc8d4d0, isDark ? 1.6 : 2.2));
  const d = new THREE.DirectionalLight(0xffffff, isDark ? 1.4 : 1.8); d.position.set(4, 6, 8); g.add(d);
  scene.add(g); return g;
}
function disposeAll(o: THREE.Object3D) { o.traverse((m: any) => { m.geometry?.dispose?.(); (Array.isArray(m.material) ? m.material : [m.material]).forEach((x: any) => x?.dispose?.()); }); o.removeFromParent(); }
const riskCol = (c: ReturnType<typeof pal>, x: number) => x < .5 ? c.ok.clone().lerp(c.warn, x * 2) : c.warn.clone().lerp(c.risk, (x - .5) * 2);

/* k1 控制网络 */
function k1({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, 0, 11); camera.lookAt(0, 0, 0);
  const c = pal(isDark), root = new THREE.Group(); scene.add(root); const L = lights(scene, isDark);
  const N = 38, pts: THREE.Vector3[] = []; let s = 7; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  for (let i = 0; i < N; i++) { const v = new THREE.Vector3(rnd() - .5, rnd() - .5, rnd() - .5).multiply(new THREE.Vector3(5, 3.8, 3)); pts.push(v); }
  const edges: [number, number][] = [];
  pts.forEach((p, i) => pts.map((q, j) => [j, p.distanceTo(q)] as const).filter(([j]) => j > i).sort((a, b) => a[1] - b[1]).slice(0, 2).forEach(([j]) => edges.push([i, j])));
  const lg = new THREE.BufferGeometry().setFromPoints(edges.flatMap(([a, b]) => [pts[a], pts[b]]));
  root.add(new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ color: c.line, transparent: true, opacity: .45 })));
  const ng = new THREE.IcosahedronGeometry(.16, 1);
  const nodes = pts.map((p, i) => { const m = new THREE.Mesh(i % 7 === 0 ? new THREE.OctahedronGeometry(.3) : ng, new THREE.MeshStandardMaterial({ color: c.base, roughness: .5, emissive: c.ok, emissiveIntensity: 0 })); m.position.copy(p); root.add(m); return m; });
  const pg = new THREE.SphereGeometry(.07, 8, 6), pulses = edges.map((e, i) => { const m = new THREE.Mesh(pg, new THREE.MeshBasicMaterial({ color: i % 5 === 0 ? c.warn : c.ok })); root.add(m); return { m, e, o: rnd(), sp: .25 + rnd() * .3 }; });
  return { update(t, px, py) {
    root.rotation.y = t * .12 + px * .2; root.rotation.x = Math.sin(t * .2) * .12 + py * .1;
    pulses.forEach(p => { const k = (t * p.sp + p.o) % 1; p.m.position.lerpVectors(pts[p.e[0]], pts[p.e[1]], k); });
    nodes.forEach((n, i) => { const k = Math.max(0, Math.sin(t * 1.3 - i * .7)); (n.material as THREE.MeshStandardMaterial).emissiveIntensity = k ** 6 * (isDark ? 1.2 : .9); n.scale.setScalar(1 + .25 * k ** 6); });
  }, dispose() { disposeAll(root); disposeAll(L); } };
}

/* k2 风险热力矩阵 */
function k2({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(9, 9.5, 12.5); camera.lookAt(0, 0.8, 0);
  const c = pal(isDark), root = new THREE.Group(); scene.add(root); const L = lights(scene, isDark);
  const n = 8, g = new THREE.BoxGeometry(.72, 1, .72); g.translate(0, .5, 0);
  const bars: { m: THREE.Mesh; i: number; j: number }[] = [];
  for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) { const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ roughness: .55 })); m.position.set((i - n / 2 + .5) * .85, 0, (j - n / 2 + .5) * .85); root.add(m); bars.push({ m, i, j }); }
  const base = new THREE.Mesh(new THREE.BoxGeometry(n * .85 + .4, .12, n * .85 + .4), new THREE.MeshStandardMaterial({ color: c.card, roughness: .8 })); base.position.y = -.07; root.add(base);
  return { update(t, px) {
    root.rotation.y = Math.sin(t * .15) * .35 + px * .2;
    bars.forEach(({ m, i, j }) => { const base = (i + j) / (2 * n - 2); const w = .5 + .5 * Math.sin(t * .9 - i * .55 + j * .35) * Math.cos(t * .4 + j * .3); const x = Math.min(1, base * .55 + w * .5 - .05); m.scale.y = .12 + Math.max(0, x) * 2.2; (m.material as THREE.MeshStandardMaterial).color.copy(riskCol(c, x)); });
  }, dispose() { disposeAll(root); disposeAll(L); } };
}

/* k3 审计链 */
function k3({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, 2.4, 13.5); camera.lookAt(0, 0, 0);
  const c = pal(isDark), root = new THREE.Group(); scene.add(root); const L = lights(scene, isDark);
  const gap = 1.7, K = 8, bg = new THREE.BoxGeometry(1.2, 1.2, 1.2), eg = new THREE.EdgesGeometry(bg);
  const tick = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-.28, 0, .61), new THREE.Vector3(-.08, -.2, .61), new THREE.Vector3(.3, .22, .61)]);
  const blocks = Array.from({ length: K }, (_, i) => { const g = new THREE.Group(); const m = new THREE.Mesh(bg, new THREE.MeshStandardMaterial({ color: i % 4 === 2 ? c.warn : c.base, roughness: .45, transparent: true })); g.add(m, new THREE.LineSegments(eg, new THREE.LineBasicMaterial({ color: c.line })), new THREE.Line(tick, new THREE.LineBasicMaterial({ color: c.ok }))); root.add(g); return g; });
  const links = Array.from({ length: K }, () => { const m = new THREE.Mesh(new THREE.TorusGeometry(.22, .05, 8, 20), new THREE.MeshStandardMaterial({ color: c.line })); m.rotation.y = Math.PI / 2; root.add(m); return m; });
  return { update(t, px, py) {
    root.rotation.y = -.35 + px * .15; root.rotation.x = .08 + py * .05;
    const T = 2.4, ph = (t / T) % 1, step = Math.floor(t / T);
    blocks.forEach((b, i) => { const k = i - ph; let x = (k - K / 2 + 1) * gap, y = 0; const last = i === K - 1; if (last) { const d = Math.min(1, ph * 1.6); y = (1 - d) ** 2 * 4; x = (K / 2 - 1 - ph) * gap; } b.position.set(x, y + Math.sin(t * 1.2 + i) * .06, 0); b.rotation.y = last ? (1 - Math.min(1, ph * 1.6)) * 1.5 : 0; const edge = Math.min(1, (x + K * gap / 2) / 2); ((b.children[0] as THREE.Mesh).material as THREE.MeshStandardMaterial).opacity = Math.max(0, edge);
      ((b.children[0] as THREE.Mesh).material as THREE.MeshStandardMaterial).color.copy((i + step) % 4 === 2 ? c.warn : c.base); });
    links.forEach((l, i) => { if (i < K - 1) { l.position.lerpVectors(blocks[i].position, blocks[i + 1].position, .5); l.visible = blocks[i + 1].position.y < .3; } else l.visible = false; });
  }, dispose() { disposeAll(root); disposeAll(L); } };
}

/* k4 制度文件层叠 */
function k4({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, 0, 10); camera.lookAt(0, 0, 0);
  const c = pal(isDark), root = new THREE.Group(); scene.add(root);
  const cv = document.createElement("canvas"); cv.width = 256; cv.height = 340; const g = cv.getContext("2d")!;
  g.fillStyle = "#" + c.card.getHexString(); g.fillRect(0, 0, 256, 340); g.fillStyle = "#" + c.line.getHexString(); g.fillRect(24, 26, 140, 16);
  for (let r = 0; r < 12; r++) { g.globalAlpha = .45; g.fillRect(24, 64 + r * 21, 150 + ((r * 37) % 60), 7); g.globalAlpha = 1; g.strokeStyle = "#" + c.ok.getHexString(); g.lineWidth = 3; g.strokeRect(210, 60 + r * 21, 12, 12); }
  const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
  const pg = new THREE.PlaneGeometry(2.6, 3.45), N = 7;
  const cards = Array.from({ length: N }, (_, i) => { const m = new THREE.Mesh(pg, new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: .92, side: THREE.DoubleSide, depthWrite: false })); const o = new THREE.Mesh(new THREE.EdgesGeometry(pg), undefined); void o; root.add(m); return m; });
  const scan = new THREE.Mesh(new THREE.PlaneGeometry(2.7, .06), new THREE.MeshBasicMaterial({ color: c.ok, transparent: true, opacity: .9 })); root.add(scan);
  const ticks = Array.from({ length: 12 }, (_, r) => { const m = new THREE.Mesh(new THREE.PlaneGeometry(.11, .11), new THREE.MeshBasicMaterial({ color: c.ok })); root.add(m); return { m, r }; });
  return { update(t, px, py) {
    root.rotation.y = px * .2; root.rotation.x = py * .1;
    const cyc = t * .25;
    cards.forEach((m, i) => { const k = ((i / N + cyc) % 1); const a = (k - .5) * 2.4; m.position.set(Math.sin(a) * 3.4, -Math.cos(a) * .9 + .6, Math.cos(a) * 1.5 - 1.2); m.rotation.set(0, -a * .6, -a * .25); m.renderOrder = Math.round(m.position.z * 10); (m.material as THREE.MeshBasicMaterial).opacity = Math.min(1, Math.sin(k * Math.PI) * 1.6) * .95; });
    const front = cards.reduce((a, b) => (b.position.z > a.position.z ? b : a));
    const s = (t * .5) % 1; scan.position.copy(front.position); scan.rotation.copy(front.rotation); scan.translateY(1.5 - s * 3.1); scan.translateZ(.02); scan.renderOrder = 999;
    ticks.forEach(({ m, r }) => { m.position.copy(front.position); m.rotation.copy(front.rotation); m.translateX(.965); m.translateY(1.25 - r * .213); m.translateZ(.02); m.renderOrder = 999; m.visible = (1.5 - s * 3.1) < (1.25 - r * .213); });
  }, dispose() { disposeAll(root); tex.dispose(); } };
}

/* k5 三道防线 */
function k5({ scene, camera, isDark }: Ctx): Built {
  camera.position.set(0, 0, 11); camera.lookAt(0, 0, 0);
  const c = pal(isDark), root = new THREE.Group(); scene.add(root); const L = lights(scene, isDark);
  const sh = new THREE.Shape(); sh.moveTo(0, 1.25); sh.bezierCurveTo(.55, 1.05, .95, 1.05, 1.05, 1.0); sh.bezierCurveTo(1.05, .1, .7, -.75, 0, -1.25); sh.bezierCurveTo(-.7, -.75, -1.05, .1, -1.05, 1.0); sh.bezierCurveTo(-.95, 1.05, -.55, 1.05, 0, 1.25);
  const cols = [c.ok, c.line, c.warn], layers = [1, 1.55, 2.1].map((s, i) => { const g = new THREE.ExtrudeGeometry(sh, { depth: .08, bevelEnabled: true, bevelSize: .03, bevelThickness: .03, bevelSegments: 2 }); g.center(); const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: cols[i], transparent: true, opacity: i === 0 ? .95 : .28 - i * .04, roughness: .4, depthWrite: i === 0 })); m.scale.setScalar(s); const e = new THREE.LineSegments(new THREE.EdgesGeometry(g, 30), new THREE.LineBasicMaterial({ color: cols[i], transparent: true, opacity: .9 })); e.scale.setScalar(s); const grp = new THREE.Group(); grp.add(m, e); grp.position.z = -i * .5; root.add(grp); return grp; });
  const P = 160, pg = new THREE.BufferGeometry(), pos = new Float32Array(P * 3), col = new Float32Array(P * 3); pg.setAttribute("position", new THREE.BufferAttribute(pos, 3)); pg.setAttribute("color", new THREE.BufferAttribute(col, 3));
  const pts = new THREE.Points(pg, new THREE.PointsMaterial({ size: .13, vertexColors: true, transparent: true, opacity: .95 })); root.add(pts);
  const ps = Array.from({ length: P }, (_, i) => ({ a: (i * 2.399) % (Math.PI * 2), o: (i * .618) % 1, stop: [1.1, 1.7, 2.3][i % 3] * 1.15 }));
  return { update(t, px, py) {
    root.rotation.y = Math.sin(t * .3) * .35 + px * .2; root.rotation.x = py * .1;
    layers.forEach((l, i) => { l.rotation.z = Math.sin(t * .5 + i) * .05; l.scale.setScalar(1 + Math.sin(t * 1.4 - i * .8) * .015); });
    ps.forEach((p, i) => { const k = (t * .18 + p.o) % 1; const r = 4.6 - k * 4.6; const rr = Math.max(r, p.stop); const hit = r < p.stop; const fade = hit ? Math.max(0, 1 - (p.stop - r) * 1.4) : Math.min(1, k * 5);
      pos[i * 3] = Math.cos(p.a) * rr * 1.1; pos[i * 3 + 1] = Math.sin(p.a) * rr; pos[i * 3 + 2] = .3;
      const cc = hit ? cols[i % 3] : c.risk; col[i * 3] = cc.r * fade; col[i * 3 + 1] = cc.g * fade; col[i * 3 + 2] = cc.b * fade;
      if (!isDark && !hit) { col[i * 3] = cc.r; col[i * 3 + 1] = cc.g; col[i * 3 + 2] = cc.b; } });
    pg.attributes.position.needsUpdate = true; pg.attributes.color.needsUpdate = true;
  }, dispose() { disposeAll(root); disposeAll(L); } };
}

export function buildHeroVariant3(v: HeroVariant3, ctx: Ctx): Built { return ({ k1, k2, k3, k4, k5 } as const)[v](ctx); }
