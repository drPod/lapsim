// Small-steps XPBD (Macklin et al. 2019, "Small Steps in Physics Simulation"):
// many substeps, one constraint iteration each, no lambda accumulation.
// One particle system holds every tissue (liver + gallbladder tet nodes, duct /
// artery rods, peritoneal fat sheet) so constraints can couple organs.
export class XPBD {
  constructor(max = 4096) {
    this.n = 0;
    this.x = new Float32Array(max * 3);     // positions
    this.p = new Float32Array(max * 3);     // predicted
    this.v = new Float32Array(max * 3);
    this.w = new Float32Array(max);         // inverse mass (0 = static)
    this.w0 = new Float32Array(max);        // original inverse mass (restored after grasp release)
    this.rest = new Float32Array(max * 3);  // rest positions (for tethers + texturing)
    this.tag = new Uint8Array(max);         // body id
    this.kin = new Map();                   // particle -> [x,y,z] kinematic target (grasped)
    // constraints
    this.dist = [];   // {a,b,r,c,alive,group}
    this.vol = [];    // {a,b,c,d,r,k}
    this.teth = [];   // {i,c,alive}  soft spring to rest position
    this.pins = [];   // {i, j, c}     zero-length link between two particles (organ coupling)
    this.gravity = [0, -2.5, -4.5];
    this.damping = 4.0;
    this.colliders = []; // capsules {a:[x,y,z], b:[x,y,z], r}
  }

  add(x, y, z, w, tag = 0) {
    const i = this.n++, k = i * 3;
    this.x[k] = this.p[k] = this.rest[k] = x;
    this.x[k + 1] = this.p[k + 1] = this.rest[k + 1] = y;
    this.x[k + 2] = this.p[k + 2] = this.rest[k + 2] = z;
    this.w[i] = this.w0[i] = w; this.tag[i] = tag;
    return i;
  }
  len(a, b) { const x = this.x; const dx = x[a * 3] - x[b * 3], dy = x[a * 3 + 1] - x[b * 3 + 1], dz = x[a * 3 + 2] - x[b * 3 + 2]; return Math.hypot(dx, dy, dz); }
  addDist(a, b, compliance, group = 0) { const c = { a, b, r: this.len(a, b), c: compliance, alive: true, group }; this.dist.push(c); return c; }
  addTet(a, b, c, d, compliance) { const t = { a, b, c, d, r: this.tetVol(this.x, a, b, c, d), k: compliance }; if (Math.abs(t.r) > 1e-12) this.vol.push(t); return t; }
  addTether(i, compliance) { const t = { i, c: compliance, alive: true }; this.teth.push(t); return t; }
  addPin(i, j, compliance) { const t = { i, j, c: compliance, alive: true }; this.pins.push(t); return t; }

  tetVol(x, a, b, c, d) {
    const ax = x[a * 3], ay = x[a * 3 + 1], az = x[a * 3 + 2];
    const bx = x[b * 3] - ax, by = x[b * 3 + 1] - ay, bz = x[b * 3 + 2] - az;
    const cx = x[c * 3] - ax, cy = x[c * 3 + 1] - ay, cz = x[c * 3 + 2] - az;
    const dx = x[d * 3] - ax, dy = x[d * 3 + 1] - ay, dz = x[d * 3 + 2] - az;
    return (bx * (cy * dz - cz * dy) - by * (cx * dz - cz * dx) + bz * (cx * dy - cy * dx)) / 6;
  }

  step(dt, substeps = 12) {
    const h = dt / substeps, { x, p, v, w, n } = this;
    const [gx, gy, gz] = this.gravity;
    const damp = Math.max(0, 1 - this.damping * h);
    for (let s = 0; s < substeps; s++) {
      for (let i = 0; i < n; i++) {
        const k = i * 3;
        if (w[i] === 0) { p[k] = x[k]; p[k + 1] = x[k + 1]; p[k + 2] = x[k + 2]; continue; }
        v[k] = (v[k] + gx * h) * damp; v[k + 1] = (v[k + 1] + gy * h) * damp; v[k + 2] = (v[k + 2] + gz * h) * damp;
        p[k] = x[k] + v[k] * h; p[k + 1] = x[k + 1] + v[k + 1] * h; p[k + 2] = x[k + 2] + v[k + 2] * h;
      }
      // grasped particles follow the jaws
      for (const [i, t] of this.kin) { const k = i * 3; p[k] = t[0]; p[k + 1] = t[1]; p[k + 2] = t[2]; }
      this.solve(h);
      this.collide();
      const ih = 1 / h;
      for (let i = 0; i < n; i++) {
        const k = i * 3;
        if (w[i] !== 0) { v[k] = (p[k] - x[k]) * ih; v[k + 1] = (p[k + 1] - x[k + 1]) * ih; v[k + 2] = (p[k + 2] - x[k + 2]) * ih; }
        x[k] = p[k]; x[k + 1] = p[k + 1]; x[k + 2] = p[k + 2];
      }
    }
  }

  solve(h) {
    const { p, w, rest } = this, h2 = h * h;
    for (const c of this.dist) {
      if (!c.alive) continue;
      const a = c.a * 3, b = c.b * 3, wa = w[c.a], wb = w[c.b], ws = wa + wb;
      if (ws === 0) continue;
      const dx = p[a] - p[b], dy = p[a + 1] - p[b + 1], dz = p[a + 2] - p[b + 2];
      const l = Math.hypot(dx, dy, dz); if (l < 1e-9) continue;
      const lam = -(l - c.r) / (ws + c.c / h2), sx = dx / l * lam, sy = dy / l * lam, sz = dz / l * lam;
      p[a] += sx * wa; p[a + 1] += sy * wa; p[a + 2] += sz * wa;
      p[b] -= sx * wb; p[b + 1] -= sy * wb; p[b + 2] -= sz * wb;
    }
    for (const t of this.vol) this.solveVol(t, h2);
    for (const t of this.teth) {
      if (!t.alive) continue;
      const i = t.i, k = i * 3, wi = w[i]; if (wi === 0) continue;
      const s = wi / (wi + t.c / h2);
      p[k] += (rest[k] - p[k]) * s; p[k + 1] += (rest[k + 1] - p[k + 1]) * s; p[k + 2] += (rest[k + 2] - p[k + 2]) * s;
    }
    for (const t of this.pins) {
      if (!t.alive) continue;
      const a = t.i * 3, b = t.j * 3, wa = w[t.i], wb = w[t.j], ws = wa + wb; if (ws === 0) continue;
      const s = 1 / (ws + t.c / h2);
      const dx = (p[b] - p[a]) * s, dy = (p[b + 1] - p[a + 1]) * s, dz = (p[b + 2] - p[a + 2]) * s;
      p[a] += dx * wa; p[a + 1] += dy * wa; p[a + 2] += dz * wa;
      p[b] -= dx * wb; p[b + 1] -= dy * wb; p[b + 2] -= dz * wb;
    }
  }

  solveVol(t, h2) {
    const { p, w } = this, ids = [t.a, t.b, t.c, t.d];
    const P = (i) => [p[i * 3], p[i * 3 + 1], p[i * 3 + 2]];
    const [x0, x1, x2, x3] = ids.map(P);
    const sub = (u, v) => [u[0] - v[0], u[1] - v[1], u[2] - v[2]];
    const cross = (u, v) => [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    // gradients of the signed volume
    const g1 = cross(sub(x2, x0), sub(x3, x0)), g2 = cross(sub(x3, x0), sub(x1, x0)), g3 = cross(sub(x1, x0), sub(x2, x0));
    for (const g of [g1, g2, g3]) { g[0] /= 6; g[1] /= 6; g[2] /= 6; }
    const g0 = [-g1[0] - g2[0] - g3[0], -g1[1] - g2[1] - g3[1], -g1[2] - g2[2] - g3[2]];
    const G = [g0, g1, g2, g3];
    let ws = 0; for (let j = 0; j < 4; j++) ws += w[ids[j]] * (G[j][0] ** 2 + G[j][1] ** 2 + G[j][2] ** 2);
    if (ws === 0) return;
    const vol = this.tetVol(p, t.a, t.b, t.c, t.d);
    const lam = -(vol - t.r) / (ws + t.k / h2);
    for (let j = 0; j < 4; j++) {
      const k = ids[j] * 3, s = lam * w[ids[j]];
      p[k] += G[j][0] * s; p[k + 1] += G[j][1] * s; p[k + 2] += G[j][2] * s;
    }
  }

  // push free particles out of instrument capsules (shafts + jaws)
  collide() {
    const { p, w, n } = this;
    for (const cap of this.colliders) {
      const [ax, ay, az] = cap.a, [bx, by, bz] = cap.b, r = cap.r;
      const ex = bx - ax, ey = by - ay, ez = bz - az, ee = ex * ex + ey * ey + ez * ez;
      for (let i = 0; i < n; i++) {
        if (w[i] === 0 || cap.skip?.has(i)) continue;
        const k = i * 3, px = p[k] - ax, py = p[k + 1] - ay, pz = p[k + 2] - az;
        const t = Math.max(0, Math.min(1, (px * ex + py * ey + pz * ez) / ee));
        const dx = px - ex * t, dy = py - ey * t, dz = pz - ez * t, d = Math.hypot(dx, dy, dz);
        if (d < r && d > 1e-9) { const s = (r - d) / d; p[k] += dx * s; p[k + 1] += dy * s; p[k + 2] += dz * s; }
      }
    }
  }

  nearest(pt, radius, filter) {
    let best = -1, bd = radius;
    for (let i = 0; i < this.n; i++) {
      if (filter && !filter(i)) continue;
      const d = Math.hypot(this.x[i * 3] - pt[0], this.x[i * 3 + 1] - pt[1], this.x[i * 3 + 2] - pt[2]);
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }
  within(pt, radius, filter) {
    const out = [];
    for (let i = 0; i < this.n; i++) {
      if (filter && !filter(i)) continue;
      const d = Math.hypot(this.x[i * 3] - pt[0], this.x[i * 3 + 1] - pt[1], this.x[i * 3 + 2] - pt[2]);
      if (d < radius) out.push(i);
    }
    return out;
  }
}
