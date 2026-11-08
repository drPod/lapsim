// Scripted "assistant + surgeon" that performs the key steps of a lap chole by
// emitting tool poses through the same fulcrum interface as the tracker:
// retract fundus, retract infundibulum, dissect Calot's triangle, clip + divide
// the cystic duct, then (deliberately) cut the cystic artery unclipped to
// demonstrate the complication pipeline (bleed + warning + vitals + voice).
// Works on live landmark positions (browser) or static ones (scripts/sim-tools.mjs).
import { tipToPose, poseToShaft, DEFAULT_TOOLS } from './tools.js';

export const LANDMARKS = ['fundus', 'infundibulum', 'ductGB', 'ductMid', 'ductCBD', 'ductCBD2', 'ductCut', 'arteryMid'];
const lerp = (a, b, t) => a + (b - a) * t;
const ease = (t) => t * t * (3 - 2 * t);
const vadd = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];

/** Steps per tool: [startTime, duration, {to, jaw, tool, energy}] — `to` is a landmark name + offset or 'rel' move. */
export const SCRIPT = {
  assist: [
    [0.0, 2.0, { to: ['fundus', [0.004, 0.004, 0.012]], jaw: 1 }],
    [2.0, 0.6, { to: ['fundus', [0.0, 0.0, 0.002]], jaw: 1 }],
    [2.6, 0.4, { jaw: 0 }],
    [3.0, 2.5, { rel: [0.006, 0.018, -0.006], jaw: 0 }], // fundus cephalad over the liver
  ],
  left: [
    [1.0, 2.0, { to: ['infundibulum', [0.006, -0.006, 0.012]], jaw: 1 }],
    [3.0, 0.7, { to: ['infundibulum', [0.0, 0.0, 0.0025]], jaw: 1 }],
    [3.7, 0.4, { jaw: 0 }],
    [4.1, 2.0, { rel: [0.012, -0.006, 0.004], jaw: 0 }], // infundibulum lateral: opens Calot's triangle
  ],
  right: [
    // blunt dissection of the peritoneum with the Maryland
    [2.0, 2.0, { tool: 'maryland', to: ['ductMid', [0.0, 0.01, 0.012]], jaw: 1 }],
    [6.0, 0.8, { to: ['ductMid', [0.0, 0.006, 0.0035]], jaw: 1 }],
    [6.8, 0.3, { jaw: 0 }],
    [7.1, 1.0, { rel: [-0.004, 0.004, 0.012], jaw: 0 }],
    [8.1, 0.3, { jaw: 1 }],
    [8.4, 0.8, { to: ['arteryMid', [0.0, 0.0, 0.0035]], jaw: 1 }],
    [9.2, 0.3, { jaw: 0 }],
    [9.5, 1.0, { rel: [0.002, -0.004, 0.012], jaw: 0 }],
    [10.5, 0.3, { jaw: 1 }],
    // hook cautery on the remaining fat (smoke)
    [10.8, 1.0, { tool: 'hook', to: ['ductMid', [0.004, 0.008, 0.006]], jaw: 1 }],
    [11.8, 1.6, { rel: [-0.006, 0.006, -0.004], jaw: 1, energy: true }],
    [13.4, 0.5, { rel: [0, 0, 0.008], jaw: 1, energy: false }],
    // clip applier: one clip on the gallbladder side, two on the CBD side
    [13.9, 1.2, { tool: 'clipper', to: ['ductGB', [0.0, 0.0, 0.008]], jaw: 1 }],
    [15.1, 0.5, { to: ['ductGB', [0, 0, 0]], jaw: 1 }], [15.6, 0.3, { jaw: 0 }], [15.9, 0.3, { jaw: 1 }],
    [16.2, 0.6, { to: ['ductCBD', [0, 0, 0.004]], jaw: 1 }],
    [16.8, 0.4, { to: ['ductCBD', [0, 0, 0]], jaw: 1 }], [17.2, 0.3, { jaw: 0 }], [17.5, 0.3, { jaw: 1 }],
    [17.8, 0.5, { to: ['ductCBD2', [0, 0, 0]], jaw: 1 }], [18.3, 0.3, { jaw: 0 }], [18.6, 0.3, { jaw: 1 }],
    [18.9, 0.6, { rel: [0, 0, 0.01], jaw: 1 }],
    // scissors: divide the duct between the clips, then cut the artery (unclipped!)
    [19.5, 1.2, { tool: 'scissors', to: ['ductCut', [0, 0, 0.006]], jaw: 1 }],
    [20.7, 0.5, { to: ['ductCut', [0, 0, 0]], jaw: 1 }], [21.2, 0.3, { jaw: 0 }], [21.5, 0.3, { jaw: 1 }],
    [21.8, 0.8, { to: ['arteryMid', [0, 0, 0.004]], jaw: 1 }],
    [22.6, 0.4, { to: ['arteryMid', [0, 0, 0]], jaw: 1 }], [23.0, 0.3, { jaw: 0 }], [23.3, 0.3, { jaw: 1 }],
    [23.6, 1.5, { rel: [0, 0, 0.015], jaw: 1 }],
  ],
};

export class Autopilot {
  /** @param abd an Abdomen (live landmarks) or {landmarks: {...}} static positions */
  constructor(abd, { script = SCRIPT, landmarks = null, variant = null } = {}) {
    // variant "bile": no clips on the duct and stop before the artery -> bile leak complication
    if (variant === 'bile') script = { ...script, right: script.right.filter(([t, , s]) => t < 13.9 || (t >= 19.5 && t < 21.8)) };
    this.abd = abd; this.script = script; this.static = landmarks; this.t = 0;
    this.poses = {}; this.track = {};
    for (const id of Object.keys(script)) {
      const d = abd?.poses?.[id] ?? DEFAULT_TOOLS[id];
      this.poses[id] = { id, ...structuredClone(d), energy: false };
      this.track[id] = { step: -1, from: null };
    }
  }
  get duration() { return Math.max(...Object.values(this.script).flat().map(([s, d]) => s + d)) + 1; }
  landmark(name) {
    if (this.static) return this.static[name];
    const A = this.abd.anat, x = this.abd.sim.x, P = (i) => [x[i * 3], x[i * 3 + 1], x[i * 3 + 2]];
    const { duct, artery } = A, n = duct.ids.length;
    switch (name) {
      case 'fundus': return P(A.landmarks.fundus);
      case 'infundibulum': return P(A.landmarks.infundibulum);
      case 'ductGB': return duct.pos(3);
      case 'ductMid': return duct.pos(Math.floor(n / 2));
      case 'ductCBD': return duct.pos(n - 3);
      case 'ductCBD2': return duct.pos(n - 6);
      case 'ductCut': return duct.pos(Math.round((3 + n - 6) / 2));
      case 'arteryMid': return artery.pos(Math.floor(artery.ids.length / 2));
      default: return [0, 0, 0];
    }
  }
  tick(dt) {
    this.t += dt;
    const out = [];
    for (const [id, steps] of Object.entries(this.script)) {
      const p = this.poses[id], tr = this.track[id];
      // find active step
      let k = -1; for (let i = 0; i < steps.length; i++) if (this.t >= steps[i][0]) k = i;
      if (k < 0) { out.push({ ...p }); continue; }
      const [start, dur, s] = steps[k];
      if (tr.step !== k) { // step begins: snapshot the starting pose
        tr.step = k; tr.from = { ...p };
        if (s.tool && s.tool !== p.tool) { p.tool = s.tool; tr.from.tool = s.tool; }
        if (s.to) tr.target = vadd(this.landmark(s.to[0]), s.to[1]);
        if (s.rel) { tr.relStart = this.currentTip(id); tr.target = vadd(tr.relStart, s.rel); }
        tr.targetPose = tr.target ? { ...tipToPose(p.port, tr.target) } : null;
      }
      const u = ease(Math.min(1, (this.t - start) / dur));
      if (s.to && !s.rel) tr.targetPose = tipToPose(p.port, vadd(this.landmark(s.to[0]), s.to[1])); // follow moving tissue
      if (tr.targetPose) for (const key of ['yaw', 'pitch', 'depth']) p[key] = lerp(tr.from[key], tr.targetPose[key], u);
      if (s.jaw !== undefined) p.jaw = lerp(tr.from.jaw, s.jaw, u);
      p.energy = !!s.energy;
      out.push({ ...p });
    }
    return out;
  }
  currentTip(id) { return poseToShaft(this.poses[id]).tip; }
}
