// Hepatobiliary anatomy for the virtual abdomen.
//  - Liver + gallbladder: LapGym (sofa_env, MIT) visual STL + tetrahedral meshes.
//    Tet nodes are XPBD particles (edge + volume constraints); the high-res visual
//    surface is skinned to the tets with barycentric coordinates (like a SOFA mapping).
//  - Cystic duct, common bile duct (CBD/CHD) and cystic artery: XPBD rods (cuttable, clippable).
//  - Peritoneal fat over Calot's triangle: tearable XPBD sheet (dissect with grasper/hook).
//  - Background: peritoneal cavity, duodenum, omentum.
// Frame ("abdomen frame", metres): origin at the gallbladder neck; the laparoscope
// looks roughly along -Z, +Y is up on the monitor, +X is right on the monitor.
import * as THREE from 'three';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { TISSUE, prepTissueGeometry } from './materials.js';

export const TAG = { LIVER: 1, GB: 2, DUCT: 3, CBD: 4, ARTERY: 5, FAT: 6 };
const A = './assets/anatomy';

// --- LapGym mm -> abdomen frame (computed from the meshes: neck, fundus, liver-bed normal)
const NECK = new THREE.Vector3(-4.525, -17.663, -12.904);
const FUNDUS = new THREE.Vector3(-77.67, 17.49, -34.42);
const OUTWARD = new THREE.Vector3(-0.01996, -0.42784, -0.90363).normalize(); // liver-bed normal (toward the scope)
const SCREEN_AXIS = new THREE.Vector3(-0.42, -0.9, 0).normalize();           // fundus->neck runs down-left on screen

export function lapgymToAbdomen() {
  const n = OUTWARD.clone();
  const a = NECK.clone().sub(FUNDUS).normalize();
  a.addScaledVector(n, -a.dot(n)).normalize();
  const B = new THREE.Matrix4().makeBasis(a, n.clone().cross(a), n);
  const z = new THREE.Vector3(0, 0, 1), s = SCREEN_AXIS.clone();
  const S = new THREE.Matrix4().makeBasis(s, z.clone().cross(s), z);
  return new THREE.Matrix4().multiply(S).multiply(B.transpose())
    .multiply(new THREE.Matrix4().makeScale(0.001, 0.001, 0.001))
    .multiply(new THREE.Matrix4().makeTranslation(-NECK.x, -NECK.y, -NECK.z));
}

// ------------------------------------------------------------------ tet bodies
class TetBody {
  constructor(sim, tag, tets, M, w = 1) {
    this.sim = sim; this.tag = tag;
    const v = new THREE.Vector3();
    this.nodes = [];
    for (let i = 0; i < tets.points.length; i += 3) {
      v.set(tets.points[i], tets.points[i + 1], tets.points[i + 2]).applyMatrix4(M);
      this.nodes.push(sim.add(v.x, v.y, v.z, w, tag));
    }
    this.tets = [];
    for (let i = 0; i < tets.tets.length; i += 4) this.tets.push(tets.tets.slice(i, i + 4).map((k) => this.nodes[k]));
  }
  addConstraints(edgeCompliance, volCompliance) {
    const seen = new Set();
    for (const t of this.tets) {
      for (let i = 0; i < 4; i++) for (let j = i + 1; j < 4; j++) {
        const a = Math.min(t[i], t[j]), b = Math.max(t[i], t[j]), key = a * 65536 + b;
        if (!seen.has(key)) { seen.add(key); this.sim.addDist(a, b, edgeCompliance, this.tag); }
      }
      if (volCompliance !== null) this.sim.addTet(t[0], t[1], t[2], t[3], volCompliance);
    }
  }
  /** Skin a visual geometry (already in abdomen frame) to the tets. */
  bind(geo) {
    const x = this.sim.x, pos = geo.attributes.position, nv = pos.count;
    this.bindTet = new Uint16Array(nv); this.bindW = new Float32Array(nv * 4); this.bindOff = new Float32Array(nv * 3);
    const P = new THREE.Vector3(), a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), d = new THREE.Vector3();
    const m = new THREE.Matrix3(), r = new THREE.Vector3();
    const inv = this.tets.map((t) => {
      a.fromArray(x, t[0] * 3); b.fromArray(x, t[1] * 3).sub(a); c.fromArray(x, t[2] * 3).sub(a); d.fromArray(x, t[3] * 3).sub(a);
      m.set(b.x, c.x, d.x, b.y, c.y, d.y, b.z, c.z, d.z);
      return { o: a.clone(), m: m.clone().invert(), cx: (b.x + c.x + d.x) / 4 + a.x, cy: (b.y + c.y + d.y) / 4 + a.y, cz: (b.z + c.z + d.z) / 4 + a.z };
    });
    for (let i = 0; i < nv; i++) {
      P.fromBufferAttribute(pos, i);
      let best = 0, bestScore = -Infinity, bw = [1, 0, 0, 0];
      for (let k = 0; k < inv.length; k++) {
        const T = inv[k];
        const dc = (P.x - T.cx) ** 2 + (P.y - T.cy) ** 2 + (P.z - T.cz) ** 2;
        if (dc > 4e-4) continue; // only tets within 20 mm
        r.copy(P).sub(T.o).applyMatrix3(T.m);
        const w0 = 1 - r.x - r.y - r.z, score = Math.min(w0, r.x, r.y, r.z);
        if (score > bestScore) { bestScore = score; best = k; bw = [w0, r.x, r.y, r.z]; if (score >= 0) break; }
      }
      // clamp extrapolated weights (visual surface outside the coarse tet mesh) and keep the
      // residual as a rest offset, so node jitter is not amplified
      const cw = bw.map((v) => Math.max(v, -0.05)), sum = cw[0] + cw[1] + cw[2] + cw[3];
      for (let k = 0; k < 4; k++) cw[k] /= sum;
      this.bindTet[i] = best; this.bindW.set(cw, i * 4);
      const t = this.tets[best];
      for (let c = 0; c < 3; c++) this.bindOff[i * 3 + c] = P.getComponent(c) - (cw[0] * x[t[0] * 3 + c] + cw[1] * x[t[1] * 3 + c] + cw[2] * x[t[2] * 3 + c] + cw[3] * x[t[3] * 3 + c]);
    }
  }
  skin(geo) {
    const x = this.sim.x, arr = geo.attributes.position.array, W = this.bindW, O = this.bindOff;
    for (let i = 0, n = this.bindTet.length; i < n; i++) {
      const t = this.tets[this.bindTet[i]], w = i * 4;
      for (let c = 0; c < 3; c++) arr[i * 3 + c] = O[i * 3 + c] + W[w] * x[t[0] * 3 + c] + W[w + 1] * x[t[1] * 3 + c] + W[w + 2] * x[t[2] * 3 + c] + W[w + 3] * x[t[3] * 3 + c];
    }
    geo.attributes.position.needsUpdate = true;
    geo.computeVertexNormals();
  }
  centroid() { const c = [0, 0, 0]; for (const i of this.nodes) for (let k = 0; k < 3; k++) c[k] += this.sim.x[i * 3 + k] / this.nodes.length; return c; }
}

// ------------------------------------------------------------------ rods (ducts, artery)
function resample(points, spacing) {
  const curve = new THREE.CatmullRomCurve3(points);
  const n = Math.max(2, Math.round(curve.getLength() / spacing));
  return curve.getSpacedPoints(n);
}

export class Rod {
  constructor(sim, scene, { name, tag, points, radius, spacing = 0.002, material, pulsate = false }) {
    Object.assign(this, { sim, name, tag, radius, pulsate });
    this.ids = resample(points, spacing).map((p) => sim.add(p.x, p.y, p.z, 1, tag));
    this.links = []; this.bends = [];
    for (let i = 0; i + 1 < this.ids.length; i++) this.links.push(sim.addDist(this.ids[i], this.ids[i + 1], 1e-7, tag));
    for (let i = 0; i + 2 < this.ids.length; i++) this.bends.push(sim.addDist(this.ids[i], this.ids[i + 2], 2e-3, tag));
    this.clips = []; this.cuts = [];
    this.material = material;
    this.meshes = [];
    this.scene = scene;
    this.radial = 14;
    this.rebuild();
  }
  pieces() {
    const out = []; let start = 0;
    const cuts = [...this.cuts].sort((a, b) => a - b);
    for (const c of cuts) { out.push([start, c]); start = c + 1; }
    out.push([start, this.ids.length - 1]);
    return out.filter(([a, b]) => b > a);
  }
  rebuild() {
    for (const m of this.meshes) { this.scene.remove(m); m.geometry.dispose(); }
    this.meshes = this.pieces().map(([a, b]) => {
      const n = b - a + 1, rings = n + 2; // + end caps
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(rings * this.radial * 3), 3));
      g.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(rings * this.radial * 3), 3));
      const rest = new Float32Array(rings * this.radial * 3);
      const idx = [];
      for (let r = 0; r + 1 < rings; r++) for (let s = 0; s < this.radial; s++) {
        const i0 = r * this.radial + s, i1 = r * this.radial + (s + 1) % this.radial, j0 = i0 + this.radial, j1 = i1 + this.radial;
        idx.push(i0, i1, j0, i1, j1, j0);
      }
      g.setIndex(idx);
      g.setAttribute('restPos', new THREE.BufferAttribute(rest, 3));
      g.setAttribute('burn', new THREE.BufferAttribute(new Float32Array(rings * this.radial), 1));
      const mesh = new THREE.Mesh(g, this.material);
      mesh.userData.range = [a, b]; mesh.frustumCulled = false;
      this.scene.add(mesh);
      return mesh;
    });
    this._restDone = false;
  }
  radiusAt(i, t) {
    let r = this.radius;
    for (const c of this.clips) { const d = Math.abs(c.idx - i); if (d < 1.5) r *= 0.45 + 0.35 * d / 1.5; } // clip squeezes the lumen
    if (this.pulsate) r *= 1 + 0.06 * Math.max(0, Math.sin(t)) ** 6;
    return r;
  }
  update(t) {
    const x = this.sim.x, v = new THREE.Vector3(), T = new THREE.Vector3(), N = new THREE.Vector3(0, 0, 1), B = new THREE.Vector3(), prevN = new THREE.Vector3(0, 1, 0);
    for (const mesh of this.meshes) {
      const [a, b] = mesh.userData.range, g = mesh.geometry, P = g.attributes.position.array, Nn = g.attributes.normal.array, R = g.attributes.restPos.array;
      const pts = [];
      for (let i = a; i <= b; i++) pts.push(new THREE.Vector3().fromArray(x, this.ids[i] * 3));
      const rings = [];
      // end caps: collapse to the centre point slightly beyond the ends
      rings.push({ p: pts[0], r: 0.0001, i: a });
      pts.forEach((p, k) => rings.push({ p, r: this.radiusAt(a + k, t), i: a + k }));
      rings.push({ p: pts[pts.length - 1], r: 0.0001, i: b });
      for (let r = 0; r < rings.length; r++) {
        const k = Math.min(Math.max(r - 1, 0), pts.length - 1);
        const p0 = pts[Math.max(k - 1, 0)], p1 = pts[Math.min(k + 1, pts.length - 1)];
        T.copy(p1).sub(p0).normalize();
        // parallel-transport frame
        N.copy(prevN).addScaledVector(T, -prevN.dot(T)); if (N.lengthSq() < 1e-8) N.set(1, 0, 0).addScaledVector(T, -T.x); N.normalize(); prevN.copy(N);
        B.copy(T).cross(N);
        for (let s = 0; s < this.radial; s++) {
          const ang = s / this.radial * Math.PI * 2, cs = Math.cos(ang), sn = Math.sin(ang);
          v.copy(N).multiplyScalar(cs).addScaledVector(B, sn);
          const o = (r * this.radial + s) * 3;
          P[o] = rings[r].p.x + v.x * rings[r].r; P[o + 1] = rings[r].p.y + v.y * rings[r].r; P[o + 2] = rings[r].p.z + v.z * rings[r].r;
          Nn[o] = v.x; Nn[o + 1] = v.y; Nn[o + 2] = v.z;
          if (!this._restDone) { R[o] = rings[r].i * 0.002; R[o + 1] = cs * this.radius; R[o + 2] = sn * this.radius; }
        }
      }
      g.attributes.position.needsUpdate = true; g.attributes.normal.needsUpdate = true;
      if (!this._restDone) g.attributes.restPos.needsUpdate = true;
    }
    this._restDone = true;
  }
  /** index of nearest particle to a point, and its distance */
  nearest(pt) {
    let best = -1, bd = Infinity;
    this.ids.forEach((id, i) => { const d = Math.hypot(this.sim.x[id * 3] - pt[0], this.sim.x[id * 3 + 1] - pt[1], this.sim.x[id * 3 + 2] - pt[2]); if (d < bd) { bd = d; best = i; } });
    return { i: best, d: bd };
  }
  cut(i) {
    i = Math.max(0, Math.min(this.ids.length - 2, i));
    if (this.cuts.includes(i)) return false;
    this.links[i].alive = false;
    if (this.bends[i]) this.bends[i].alive = false;
    if (this.bends[i - 1]) this.bends[i - 1].alive = false;
    // relax the tethers around the cut so the ends spring apart a little
    this.cuts.push(i); this.rebuild();
    return true;
  }
  pos(i) { const id = this.ids[Math.max(0, Math.min(this.ids.length - 1, i))]; return [this.sim.x[id * 3], this.sim.x[id * 3 + 1], this.sim.x[id * 3 + 2]]; }
}

// ------------------------------------------------------------------ fat sheet over Calot's triangle
class FatSheet {
  constructor(sim, scene, { origin, ex, ey, nx, ny, attachTo, conform }) {
    this.sim = sim; this.nx = nx; this.ny = ny;
    this.ids = [];
    const p = new THREE.Vector3();
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      p.copy(origin).addScaledVector(ex, i / (nx - 1)).addScaledVector(ey, j / (ny - 1));
      // gentle billow toward the scope + lumpy thickness
      const u = i / (nx - 1), v = j / (ny - 1);
      if (conform) conform(p);
      p.z += 0.0012 * Math.sin(Math.PI * u) * Math.sin(Math.PI * v) + 0.0008 * Math.sin(u * 23 + v * 7) * Math.sin(v * 19 - u * 5);
      this.ids.push(sim.add(p.x, p.y, p.z, 1, TAG.FAT));
    }
    const id = (i, j) => this.ids[j * nx + i];
    this.edges = new Map();
    const link = (a, b, c) => { const k = Math.min(a, b) + ':' + Math.max(a, b); if (!this.edges.has(k)) this.edges.set(k, sim.addDist(a, b, c, TAG.FAT)); };
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      if (i + 1 < nx) link(id(i, j), id(i + 1, j), 2e-6);
      if (j + 1 < ny) link(id(i, j), id(i, j + 1), 2e-6);
      if (i + 1 < nx && j + 1 < ny) { link(id(i, j), id(i + 1, j + 1), 2e-5); link(id(i + 1, j), id(i, j + 1), 2e-5); }
      if (i + 2 < nx) link(id(i, j), id(i + 2, j), 4e-4); // bending: no sharp folds
      if (j + 2 < ny) link(id(i, j), id(i, j + 2), 4e-4);
    }
    for (const i of this.ids) sim.addTether(i, 6e-3);
    // attach the sheet's border and underside to nearby structures (areolar tissue)
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const pid = id(i, j), border = i === 0 || j === 0 || i === nx - 1 || j === ny - 1;
      const q = [sim.x[pid * 3], sim.x[pid * 3 + 1], sim.x[pid * 3 + 2]];
      const near = sim.nearest(q, border ? 0.008 : 0.0045, (k) => attachTo.has(sim.tag[k]));
      if (near >= 0) this.edges.set('pin' + pid, Object.assign(sim.addDist(pid, near, 5e-5, TAG.FAT), { pin: true }));
    }
    // triangles
    this.tris = [];
    for (let j = 0; j + 1 < ny; j++) for (let i = 0; i + 1 < nx; i++) {
      const a = id(i, j), b = id(i + 1, j), c = id(i, j + 1), d = id(i + 1, j + 1);
      this.tris.push([a, b, d], [a, d, c]);
    }
    const n = this.ids.length;
    this.geo = new THREE.BufferGeometry();
    this.geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    const rest = new Float32Array(n * 3); this.ids.forEach((k, i) => rest.set(sim.rest.subarray(k * 3, k * 3 + 3), i * 3));
    prepTissueGeometry(this.geo, rest);
    this.local = new Map(this.ids.map((k, i) => [k, i]));
    this.mesh = new THREE.Mesh(this.geo, TISSUE.fat({ side: THREE.DoubleSide, transparent: true, opacity: 0.86, depthWrite: false, colA: 0xc8902c, colB: 0xf2cf6a, spots: 0.3, spotScale: 420, scale: 140, veinAmt: 0.45, bump: 0.0016, bumpScale: 320 }));
    this.mesh.frustumCulled = false; this.mesh.renderOrder = 2;
    scene.add(this.mesh);
    this.rebuildIndex();
  }
  edge(a, b) { return this.edges.get(Math.min(a, b) + ':' + Math.max(a, b)); }
  rebuildIndex() {
    const idx = [];
    for (const [a, b, c] of this.tris) {
      const e = [this.edge(a, b), this.edge(b, c), this.edge(a, c)];
      if (e.every((x) => !x || x.alive)) idx.push(this.local.get(a), this.local.get(b), this.local.get(c));
    }
    this.geo.setIndex(idx);
    this.alive = idx.length / 3;
  }
  update() {
    const x = this.sim.x, P = this.geo.attributes.position.array;
    let torn = false;
    for (const c of this.edges.values()) {
      if (!c.alive) continue;
      const l = this.sim.len(c.a, c.b);
      if (l > c.r * (c.pin ? 3.5 : 1.9) + 0.0015) { c.alive = false; torn = true; } // blunt dissection tears areolar tissue
    }
    if (torn) this.rebuildIndex();
    this.ids.forEach((k, i) => { P[i * 3] = x[k * 3]; P[i * 3 + 1] = x[k * 3 + 1]; P[i * 3 + 2] = x[k * 3 + 2]; });
    this.geo.attributes.position.needsUpdate = true;
    this.geo.computeVertexNormals();
  }
  /** cautery / scissors: destroy tissue within r of pt */
  destroy(pt, r) {
    let n = 0;
    for (const c of this.edges.values()) {
      if (!c.alive) continue;
      const x = this.sim.x, mx = (x[c.a * 3] + x[c.b * 3]) / 2 - pt[0], my = (x[c.a * 3 + 1] + x[c.b * 3 + 1]) / 2 - pt[1], mz = (x[c.a * 3 + 2] + x[c.b * 3 + 2]) / 2 - pt[2];
      if (mx * mx + my * my + mz * mz < r * r) { c.alive = false; n++; }
    }
    if (n) this.rebuildIndex();
    return n;
  }
  burnAt(pt, r, amt) {
    const b = this.geo.attributes.burn, x = this.sim.x;
    this.ids.forEach((k, i) => { const d = Math.hypot(x[k * 3] - pt[0], x[k * 3 + 1] - pt[1], x[k * 3 + 2] - pt[2]); if (d < r) b.array[i] = Math.min(1, b.array[i] + amt * (1 - d / r)); });
    b.needsUpdate = true;
  }
}

// ------------------------------------------------------------------ build everything
export async function buildAnatomy(sim, scene, { fat = true } = {}) {
  const M = lapgymToAbdomen();
  const stl = new STLLoader();
  const [liverTets, gbTets, liverStl, gbStl] = await Promise.all([
    fetch(`${A}/liver_tets.json`).then((r) => r.json()), fetch(`${A}/gallbladder_tets.json`).then((r) => r.json()),
    stl.loadAsync(`${A}/liver_visual.stl`), stl.loadAsync(`${A}/gallbladder_visual.stl`),
  ]);
  const prepVisual = (g) => {
    g.deleteAttribute('normal');
    const m = mergeVertices(g, 1e-3); m.applyMatrix4(M); m.computeVertexNormals();
    return prepTissueGeometry(m);
  };
  const liverGeo = prepVisual(liverStl), gbGeo = prepVisual(gbStl);

  // liver: stiff, anchored away from the gallbladder bed (held by ligaments / retractor)
  const liver = new TetBody(sim, TAG.LIVER, liverTets, M);
  liver.addConstraints(2e-7, 0);
  const gb = new TetBody(sim, TAG.GB, gbTets, M);
  gb.addConstraints(3e-7, 0);
  const gbC = gb.centroid();
  for (const i of liver.nodes) {
    const d = Math.hypot(sim.x[i * 3] - gbC[0], sim.x[i * 3 + 1] - gbC[1], sim.x[i * 3 + 2] - gbC[2]);
    if (d > 0.075) { sim.w[i] = sim.w0[i] = 0; } else sim.addTether(i, d > 0.05 ? 1e-6 : 2e-5);
  }
  // gallbladder: attached to the liver bed (cystic plate) where it touches the liver; mild shape memory elsewhere
  const lp = liverGeo.attributes.position.array;
  gb.bed = new Map();
  for (const i of gb.nodes) {
    const q = sim.x.subarray(i * 3, i * 3 + 3);
    let dmin = Infinity; for (let k = 0; k < lp.length; k += 9) dmin = Math.min(dmin, (lp[k] - q[0]) ** 2 + (lp[k + 1] - q[1]) ** 2 + (lp[k + 2] - q[2]) ** 2);
    if (Math.sqrt(dmin) < 0.006) gb.bed.set(i, sim.addTether(i, 2e-6));
    else sim.addTether(i, 2e-4);
  }
  liver.bind(liverGeo); gb.bind(gbGeo);
  const liverMesh = new THREE.Mesh(liverGeo, TISSUE.liver());
  const gbMesh = new THREE.Mesh(gbGeo, TISSUE.gallbladder());
  liverMesh.name = 'liver'; gbMesh.name = 'gallbladder';
  for (const m of [liverMesh, gbMesh]) { m.frustumCulled = false; scene.add(m); }

  // height of the liver surface under (x, y), seen from the scope side (+Z)
  const liverRay = new THREE.Raycaster(); const liverOnly = new THREE.Mesh(liverGeo); liverOnly.updateMatrixWorld();
  const gbOnly = new THREE.Mesh(gbGeo); gbOnly.updateMatrixWorld();
  const surfaceZ = (x, y, withGB = false) => {
    liverRay.set(new THREE.Vector3(x, y, 0.2), new THREE.Vector3(0, 0, -1));
    const hits = liverRay.intersectObjects(withGB ? [liverOnly, gbOnly] : [liverOnly]);
    return hits.length ? hits[0].point.z : -0.05;
  };
  const lift = (v, clearance) => { v.z = Math.max(v.z, surfaceZ(v.x, v.y) + clearance); return v; };

  // gallbladder neck tip (the extreme visual vertex along the fundus->neck axis)
  const axis = SCREEN_AXIS.clone();
  let tip = new THREE.Vector3(), best = -Infinity; const tv = new THREE.Vector3();
  for (let i = 0; i < gbGeo.attributes.position.count; i++) { tv.fromBufferAttribute(gbGeo.attributes.position, i); const s = tv.dot(axis); if (s > best) { best = s; tip.copy(tv); } }
  const V = (x, y, z) => new THREE.Vector3(x, y, z);

  // Calot's triangle: cystic duct (neck -> CBD), CHD/CBD, cystic artery (RHA -> gallbladder)
  const J = lift(V(-0.021, -0.03, -0.006), 0.005);
  const duct = new Rod(sim, scene, {
    name: 'cystic duct', tag: TAG.DUCT, radius: 0.0019, material: TISSUE.duct(),
    points: [tip.clone().addScaledVector(axis, -0.002), tip.clone().add(V(-0.005, -0.009, 0.001)), V(-0.012, -0.019, -0.001), V(-0.017, -0.026, -0.004), J.clone().add(V(0.002, 0.001, 0))].map((v, i) => (i ? lift(v, 0.0035) : v)),
  });
  const cbd = new Rod(sim, scene, {
    name: 'common bile duct', tag: TAG.CBD, radius: 0.0032, spacing: 0.003, material: TISSUE.duct(),
    points: [V(-0.04, 0.03, -0.022), V(-0.032, 0.006, -0.012), J, V(-0.017, -0.055, -0.006), V(-0.012, -0.085, -0.014)].map((v) => lift(v, 0.0045)),
  });
  const artery = new Rod(sim, scene, {
    name: 'cystic artery', tag: TAG.ARTERY, radius: 0.0013, material: TISSUE.artery(), pulsate: true,
    points: [V(-0.036, -0.004, -0.02), V(-0.026, -0.006, -0.01), V(-0.016, -0.004, -0.004), V(-0.007, 0.0, -0.003), V(0.0, 0.004, -0.004)].map((v, i) => (i ? lift(v, 0.0025) : v)),
  });
  // rods are held by areolar tissue; ends anchored
  for (const r of [duct, cbd, artery]) for (const id of r.ids) sim.addTether(id, r === cbd ? 2e-5 : 4e-4);
  for (const k of [0, cbd.ids.length - 1]) { const id = cbd.ids[k]; sim.w[id] = sim.w0[id] = 0; }
  { const id = artery.ids[0]; sim.w[id] = sim.w0[id] = 0; }
  const gbNear = (p) => sim.nearest(p, 0.03, (k) => sim.tag[k] === TAG.GB);
  sim.addDist(duct.ids[0], gbNear(duct.pos(0)), 1e-7, TAG.DUCT); // links keep their rest length (tet nodes are coarse)
  sim.addDist(duct.ids[duct.ids.length - 1], cbd.ids[cbd.nearest(duct.pos(duct.ids.length - 1)).i], 1e-7, TAG.DUCT);
  sim.addDist(artery.ids[artery.ids.length - 1], gbNear(artery.pos(artery.ids.length - 1)), 1e-7, TAG.ARTERY);

  // fat / peritoneum over the triangle
  let sheet = null;
  const rodTop = (x, y) => { let z = -1; for (const r of [duct, cbd, artery]) for (const id of r.ids) { const d = Math.hypot(sim.x[id * 3] - x, sim.x[id * 3 + 1] - y); if (d < r.radius + 0.002) z = Math.max(z, sim.x[id * 3 + 2] + r.radius); } return z; };
  const conform = (p) => { p.z = Math.max(surfaceZ(p.x, p.y, true), rodTop(p.x, p.y)) + 0.003; return p; };
  if (fat) sheet = new FatSheet(sim, scene, { origin: V(-0.029, -0.029, 0), ex: V(0.032, 0.004, 0), ey: V(0.006, 0.036, 0), nx: 16, ny: 18, conform, attachTo: new Set([TAG.GB, TAG.DUCT, TAG.CBD, TAG.ARTERY, TAG.LIVER]) });

  buildBackground(scene);

  const landmarks = () => ({
    fundus: gbFundusNode(), infundibulum: gbNear(tip.clone().addScaledVector(axis, -0.016).toArray()),
    neck: duct.ids[0],
  });
  function gbFundusNode() { let bi = -1, bs = Infinity; for (const i of gb.nodes) { const s = sim.x[i * 3] * axis.x + sim.x[i * 3 + 1] * axis.y; if (s < bs) { bs = s; bi = i; } } return bi; }

  return {
    liver, gb, liverMesh, gbMesh, liverGeo, gbGeo, duct, cbd, artery, rods: [duct, cbd, artery], sheet, landmarks: landmarks(),
    update(t, hr) {
      liver.skin(liverGeo); gb.skin(gbGeo);
      const ph = t * Math.PI * 2 * hr / 60;
      for (const r of [duct, cbd, artery]) r.update(ph);
      sheet?.update();
    },
    burnVisual(pt, r, amt) {
      for (const g of [liverGeo, gbGeo]) {
        const P = g.attributes.position.array, B = g.attributes.burn;
        let hit = false;
        for (let i = 0; i < B.count; i++) {
          const d = Math.hypot(P[i * 3] - pt[0], P[i * 3 + 1] - pt[1], P[i * 3 + 2] - pt[2]);
          if (d < r) { B.array[i] = Math.min(1, B.array[i] + amt * (1 - d / r)); hit = true; }
        }
        if (hit) B.needsUpdate = true;
      }
      sheet?.burnAt(pt, r, amt);
    },
  };
}

function buildBackground(scene) {
  // peritoneal cavity (inside of a big lumpy sphere)
  const cav = new THREE.SphereGeometry(0.22, 96, 64);
  const p = cav.attributes.position, v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) { v.fromBufferAttribute(p, i); v.multiplyScalar(1 + 0.04 * Math.sin(v.x * 40) * Math.sin(v.y * 37) * Math.sin(v.z * 31)); p.setXYZ(i, v.x, v.y, v.z); }
  cav.computeVertexNormals(); prepTissueGeometry(cav);
  const cavity = new THREE.Mesh(cav, TISSUE.peritoneum()); cavity.position.set(0, 0.02, 0.02); cavity.name = 'cavity';
  scene.add(cavity);
  // duodenum: thick tube sweeping under the CBD
  const duoCurve = new THREE.CatmullRomCurve3([V3(0.06, -0.07, -0.03), V3(0.02, -0.075, -0.02), V3(-0.02, -0.085, -0.02), V3(-0.06, -0.075, -0.035), V3(-0.09, -0.05, -0.05)]);
  const duo = prepTissueGeometry(new THREE.TubeGeometry(duoCurve, 80, 0.016, 32));
  scene.add(new THREE.Mesh(duo, TISSUE.bowel()));
  // omentum / fat lumps along the bottom and right
  const fatMat = TISSUE.fat();
  const lumps = [[0.03, -0.055, 0.0, 0.03], [0.07, -0.04, -0.01, 0.028], [0.05, -0.085, 0.01, 0.035], [-0.005, -0.06, 0.006, 0.02], [0.095, -0.0, -0.03, 0.03], [-0.07, -0.09, 0.0, 0.035]];
  for (const [x, y, z, r] of lumps) {
    const g = new THREE.SphereGeometry(r, 48, 32), q = g.attributes.position;
    for (let i = 0; i < q.count; i++) { v.fromBufferAttribute(q, i); const k = 1 + 0.08 * Math.sin(v.x * 140) * Math.sin(v.y * 120 + 1) * Math.sin(v.z * 150 + 2) + 0.04 * Math.sin(v.x * 330 + v.z * 200); v.multiplyScalar(k); v.y *= 0.6; q.setXYZ(i, v.x, v.y, v.z); }
    g.computeVertexNormals(); g.translate(x, y, z); prepTissueGeometry(g);
    scene.add(new THREE.Mesh(g, fatMat));
  }
}
const V3 = (x, y, z) => new THREE.Vector3(x, y, z);
