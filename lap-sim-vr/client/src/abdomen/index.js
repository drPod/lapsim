// The virtual abdomen: XPBD tissue + instruments, seen through a virtual
// laparoscope and rendered (HDR) into a texture that the OR monitor displays.
// Complications detected here (bleeding, bile leak, CBD injury, smoke) are
// emitted as normal director events, so overlays, vitals and agents all see them.
import * as THREE from 'three';
import { XPBD } from './xpbd.js';
import { buildAnatomy, TAG } from './anatomy.js';
import { DEFAULT_TOOLS, makeInstrument, makeClip, applyPoseToGroup, poseToShaft } from './tools.js';
import { Autopilot, LANDMARKS } from './autopilot.js';

const GRASPERS = new Set(['grasper', 'maryland']);

export class Abdomen {
  constructor({ width = 1280, height = 720, fat = true, onEvent = () => {}, scopeAngle = 0 } = {}) {
    Object.assign(this, { width, height, fat, onEvent, scopeAngle });
    this.t = 0; this.hr = 78;
    this.ready = false;
    this.lastExternal = 0;
    this.stats = { clips: 0, cuts: 0 };
  }

  async init() {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x030101);
    this.sim = new XPBD(4096);
    this.anat = await buildAnatomy(this.sim, this.scene, { fat: this.fat });
    this.camera = new THREE.PerspectiveCamera(70, this.width / this.height, 0.002, 0.6);
    this.camera.matrixAutoUpdate = false;
    // the laparoscope's light guide: a coaxial spotlight with strong falloff
    this.light = new THREE.SpotLight(0xfff1e2, 0.04, 0.6, 1.0, 0.8, 2);
    this.light.position.set(0, 0, 0); this.light.target.position.set(0, 0, -1);
    this.camera.add(this.light, this.light.target);
    this.scene.add(this.camera);
    this.scene.add(new THREE.HemisphereLight(0xffe8dd, 0x220808, 0.02));
    this.rt = new THREE.WebGLRenderTarget(this.width, this.height, { samples: 4, type: THREE.HalfFloatType });
    this.poses = structuredClone(DEFAULT_TOOLS);
    this.inst = {}; this.state = {};
    for (const id of Object.keys(this.poses)) if (id !== 'scope') this.spawnTool(id);
    this.clips = [];
    this.fired = new Set();
    this.lastSmoke = -10;
    this.ready = true;
    return this;
  }

  get texture() { return this.rt.texture; }

  spawnTool(id) {
    if (this.inst[id]) this.scene.remove(this.inst[id]);
    const g = makeInstrument(THREE, this.poses[id].tool);
    this.scene.add(g); this.inst[id] = g;
    this.release(id);
    this.state[id] = { grabbed: [], closed: false, type: this.poses[id].tool };
  }

  /** Apply poses from any source: [{id, port, tool, yaw, pitch, roll, depth, jaw, energy}] */
  setPoses(list, external = true) {
    for (let p of list) {
      if (!p?.id) continue;
      // a tracker may name tools by port ("epigastric"): drive the instrument already in that port
      if (!this.poses[p.id] && p.port) { const ex = Object.keys(this.poses).find((k) => this.poses[k].port === p.port); if (ex) p = { ...p, id: ex }; }
      const cur = this.poses[p.id] || (this.poses[p.id] = { port: p.port || 'epigastric', tool: p.tool || 'grasper', yaw: 0, pitch: 0, roll: 0, depth: 0.08, jaw: 1 });
      Object.assign(cur, p);
      if (p.id !== 'scope' && (!this.inst[p.id] || this.state[p.id].type !== cur.tool)) this.spawnTool(p.id);
    }
    if (external) this.lastExternal = performance.now();
  }

  release(id) {
    const st = this.state[id]; if (!st) return;
    for (const g of st.grabbed) { this.sim.kin.delete(g.i); this.sim.w[g.i] = this.sim.w0[g.i]; }
    st.grabbed = [];
  }

  toolMatrix(id) { this.inst[id].updateMatrixWorld(true); return this.inst[id].matrix; }

  // ------------------------------------------------------------------ interactions
  interact(id, pose, shaft) {
    const st = this.state[id], sim = this.sim, tip = shaft.tip;
    const closed = (pose.jaw ?? 1) < 0.25, opened = (pose.jaw ?? 1) > 0.4;
    const closing = closed && !st.closed;
    if (closed) st.closed = true; else if (opened) st.closed = false;
    const type = pose.tool;

    if (GRASPERS.has(type)) {
      if (closing) {
        const cand = sim.within(tip, 0.009, (i) => sim.w0[i] > 0 && !sim.kin.has(i))
          .map((i) => ({ i, d: Math.hypot(sim.x[i * 3] - tip[0], sim.x[i * 3 + 1] - tip[1], sim.x[i * 3 + 2] - tip[2]) }))
          .sort((a, b) => a.d - b.d);
        // the peritoneal veil lies on top: if it's within reach, that's what the jaws catch
        const fat = cand.filter((c) => sim.tag[c.i] === TAG.FAT && c.d < 0.006);
        const pick = fat.length ? fat.slice(0, 4) : cand.filter((c) => c.d < cand[0].d + 0.003).slice(0, 3);
        if (pick.length) {
          const inv = this.toolMatrix(id).clone().invert();
          for (const c of pick) {
            const local = new THREE.Vector3().fromArray(sim.x, c.i * 3).applyMatrix4(inv);
            st.grabbed.push({ i: c.i, local });
            sim.w[c.i] = 0;
          }
        }
      }
      if (opened && st.grabbed.length) this.release(id);
    }

    if (type === 'clipper' && closing) {
      const hit = this.nearestRod(tip, 0.006);
      if (hit && !hit.rod.clips.some((c) => Math.abs(c.idx - hit.i) < 1.2) && !hit.rod.cuts.some((c) => Math.abs(c - hit.i) < 1)) {
        const clip = { rod: hit.rod, idx: hit.i, leg: new THREE.Vector3(...shaft.dir), mesh: makeClip(THREE) };
        hit.rod.clips.push(clip); this.clips.push(clip); this.scene.add(clip.mesh);
        this.stats.clips++;
        this.onEvent({ type: 'sim', action: 'clip', target: hit.rod.name, index: hit.i, total: hit.rod.clips.length });
      }
    }

    if (type === 'scissors' && closing) {
      const hit = this.nearestRod(tip, 0.006);
      if (hit) {
        const i = this.segmentAt(hit.rod, tip);
        if (hit.rod.clips.some((c) => Math.abs(c.idx - (i + 0.5)) < 0.6)) {
          this.say('Scrub nurse', 'You are on the clip, come off it a little.');
        } else if (hit.rod.cut(i)) {
          this.stats.cuts++;
          this.onRodCut(hit.rod, i, tip);
        }
      } else if (this.anat.sheet?.destroy(tip, 0.003)) {
        // sharp dissection of peritoneum
      } else if (sim.nearest(tip, 0.0035, (k) => sim.tag[k] === TAG.GB) >= 0) {
        this.complication('gb-perforation', () => {
          this.emit({ type: 'bile', intensity: 0.3, origin: this.screenUV(tip) });
          this.say('Attending', 'You have holed the gallbladder. Suction, and grab the hole.');
        });
      } else if (sim.nearest(tip, 0.005, (k) => sim.tag[k] === TAG.LIVER) >= 0) {
        this.complication('liver-laceration', () => this.emit({ type: 'bleed', intensity: 0.3, pulsatile: false, origin: this.screenUV(tip) }));
      }
    }

    if (type === 'hook' && pose.energy) {
      const hp = new THREE.Vector3(0, 0.0025, -0.019).applyMatrix4(this.toolMatrix(id)).toArray();
      const near = sim.nearest(hp, 0.004);
      if (near >= 0) {
        this.anat.sheet?.destroy(hp, 0.0026);
        this.anat.burnVisual(hp, 0.004, 0.05);
        for (const [node, teth] of this.anat.gb.bed) {
          if (teth.alive && Math.hypot(sim.x[node * 3] - hp[0], sim.x[node * 3 + 1] - hp[1], sim.x[node * 3 + 2] - hp[2]) < 0.007) teth.alive = false;
        }
        const r = this.nearestRod(hp, 0.0026);
        if (r) {
          if (r.rod === this.anat.cbd) this.complication('cbd-thermal', () => { this.emit({ type: 'warning', text: 'Thermal injury — common bile duct', level: 'critical' }); this.say('Attending', 'Stop! You are burning the common duct.'); });
          else { const i = this.segmentAt(r.rod, hp); if (!r.rod.clips.some((c) => Math.abs(c.idx - i) < 1) && r.rod.cut(i)) { this.stats.cuts++; this.onRodCut(r.rod, i, hp, 'cautery'); } }
        }
        if (this.t - this.lastSmoke > 1.2) { this.lastSmoke = this.t; this.emit({ type: 'smoke', intensity: 0.45, durationMs: 2500 }); }
      }
    }
  }

  nearestRod(pt, radius) {
    let best = null;
    for (const rod of this.anat.rods) { const h = rod.nearest(pt); if (h.d < radius && (!best || h.d < best.d)) best = { rod, ...h }; }
    return best;
  }
  segmentAt(rod, pt) {
    const { i } = rod.nearest(pt);
    const d = (k) => (k < 0 || k >= rod.ids.length ? Infinity : Math.hypot(...rod.pos(k).map((v, j) => v - pt[j])));
    return d(i - 1) < d(i + 1) ? i - 1 : i;
  }

  onRodCut(rod, seg, pt, how = 'scissors') {
    const { duct, cbd, artery } = this.anat, uv = this.screenUV(pt);
    // proximal = patient side: artery index 0 (hepatic artery), duct last index (CBD)
    const clipsBefore = rod.clips.filter((c) => c.idx <= seg).length, clipsAfter = rod.clips.filter((c) => c.idx > seg).length;
    this.onEvent({ type: 'sim', action: 'cut', target: rod.name, index: seg, how });
    if (rod === artery) {
      if (!clipsBefore) {
        this.emit({ type: 'bleed', intensity: 0.5, pulsatile: true, origin: uv });
        this.emit({ type: 'warning', text: 'Arterial bleeding — cystic artery', level: 'critical' });
        this.emit({ type: 'vitals', hr: 112, bp: '96/58', transitionMs: 15000 });
        this.say('Attending', "That's the artery. Grasp it, suction, and get a clip on it.");
      } else if (!clipsAfter) {
        this.emit({ type: 'bleed', intensity: 0.18, pulsatile: false, origin: uv });
        this.say('Attending', 'Some back-bleeding from the gallbladder side. Fine, but clip both sides next time.');
      } else this.say('Attending', 'Artery divided between clips. Good.');
    } else if (rod === duct) {
      if (!clipsAfter) {
        this.emit({ type: 'bile', intensity: 0.32, origin: uv });
        this.emit({ type: 'warning', text: 'Bile leak — cystic duct stump', level: 'warning' });
        this.say('Attending', "The stump isn't clipped. Bile everywhere.");
      } else if (!clipsBefore) {
        this.emit({ type: 'bile', intensity: 0.2, origin: uv });
        this.say('Scrub nurse', 'Bile spilling from the gallbladder side.');
      } else this.say('Attending', clipsAfter >= 2 ? 'Two clips on the stay side, duct divided. Nice.' : 'Only one clip on the stump. I would want two.');
    } else if (rod === cbd) {
      this.emit({ type: 'bile', intensity: 0.45, origin: uv });
      this.emit({ type: 'warning', text: 'Common bile duct transected', level: 'critical' });
      this.say('Attending', "Stop. That's the common bile duct. Call hepatobiliary.");
    }
  }

  complication(key, fn) { if (!this.fired.has(key)) { this.fired.add(key); fn(); } }
  emit(ev) { this.onEvent(ev); }
  say(speaker, text) { this.onEvent({ type: 'say', speaker, text }); }
  screenUV(pt) { const v = new THREE.Vector3(...pt).project(this.camera); return { x: +Math.min(1, Math.max(0, (v.x + 1) / 2)).toFixed(3), y: +Math.min(1, Math.max(0, (1 - v.y) / 2)).toFixed(3) }; }

  // ------------------------------------------------------------------ frame
  update(dt) {
    if (!this.ready) return;
    dt = Math.min(dt, 1 / 30);
    this.t += dt;
    const sim = this.sim;
    sim.colliders.length = 0;
    for (const [id, g] of Object.entries(this.inst)) {
      const pose = this.poses[id];
      const shaft = applyPoseToGroup(THREE, g, pose, this.t);
      const st = this.state[id];
      const skip = new Set(st.grabbed.map((x) => x.i));
      // jawed instruments: only the shaft pushes tissue (tissue must be able to get between open jaws)
      const jawEnd = shaft.hinge.map((v, k) => v + shaft.dir[k] * (pose.tool === 'hook' ? 0.012 : -0.002));
      const back = shaft.hinge.map((v, k) => v - shaft.dir[k] * 0.2);
      if (!this.noCollide) sim.colliders.push({ a: jawEnd, b: back, r: pose.tool === 'hook' ? 0.0018 : 0.0028, skip });
      this.interact(id, pose, shaft);
      if (st.grabbed.length) {
        const M = this.toolMatrix(id), v = new THREE.Vector3();
        for (const gr of st.grabbed) { v.copy(gr.local).applyMatrix4(M); sim.kin.set(gr.i, [v.x, v.y, v.z]); }
      }
    }
    if (!this.paused) sim.step(dt, 10);
    this.anat.update(this.t, this.hr);
    for (const c of this.clips) this.placeClip(c);
    // laparoscope (0° or angled) from the scope pose
    const s = poseToShaft(this.poses.scope);
    const fwd = new THREE.Vector3(...s.dir), up = new THREE.Vector3(...s.up);
    if (this.scopeAngle) fwd.applyAxisAngle(new THREE.Vector3().crossVectors(fwd, up).normalize(), this.scopeAngle);
    const z = fwd.clone().negate(), x = new THREE.Vector3().crossVectors(up, z).normalize(), y = new THREE.Vector3().crossVectors(z, x);
    this.camera.matrix.makeBasis(x, y, z).setPosition(...s.hinge);
    if (this.debugCam) this.camera.matrix.lookAt(new THREE.Vector3(...this.debugCam.pos), new THREE.Vector3(...this.debugCam.at), new THREE.Vector3(0, 1, 0)).setPosition(...this.debugCam.pos);
    this.camera.matrixWorldNeedsUpdate = true;
    this.camera.updateMatrixWorld(true);
  }

  placeClip(c) {
    const rod = c.rod, i = Math.round(c.idx);
    const p = new THREE.Vector3(...rod.pos(i));
    const T = new THREE.Vector3(...rod.pos(i + 1)).sub(new THREE.Vector3(...rod.pos(i - 1))).normalize();
    const X = c.leg.clone().addScaledVector(T, -c.leg.dot(T)).normalize(), Y = new THREE.Vector3().crossVectors(T, X);
    c.mesh.matrix.makeBasis(X, Y, T).setPosition(p.addScaledVector(X, -0.0045));
    c.mesh.matrixAutoUpdate = false; c.mesh.matrixWorldNeedsUpdate = true;
  }

  /** Reflection environment: the inside of a reddish abdomen with the scope light — metals need it. */
  buildEnv(renderer) {
    const es = new THREE.Scene();
    const g = new THREE.SphereGeometry(1, 32, 16), col = [];
    const c = new THREE.Color();
    for (let i = 0; i < g.attributes.position.count; i++) { const y = g.attributes.position.getY(i); c.setRGB(0.35 + 0.15 * y, 0.08 + 0.03 * y, 0.06); col.push(c.r, c.g, c.b); }
    g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    es.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.BackSide })));
    const lamp = new THREE.Mesh(new THREE.CircleGeometry(0.25, 24), new THREE.MeshBasicMaterial({ color: new THREE.Color(6, 5.6, 5) }));
    lamp.position.set(0, 0, 0.9); lamp.lookAt(0, 0, 0); es.add(lamp);
    const pm = new THREE.PMREMGenerator(renderer);
    this.scene.environment = pm.fromScene(es, 0.02).texture;
    this.scene.environmentIntensity = 0.18;
    pm.dispose();
  }

  render(renderer) {
    if (!this.ready) return;
    if (!this.scene.environment) this.buildEnv(renderer);
    const xr = renderer.xr.enabled, prev = renderer.getRenderTarget();
    renderer.xr.enabled = false;
    renderer.setRenderTarget(this.rt);
    renderer.render(this.scene, this.camera);
    renderer.setRenderTarget(prev);
    renderer.xr.enabled = xr;
  }

  /** Landmark positions used by the autopilot (written to landmarks.json for scripts/sim-tools.mjs). */
  landmarkPositions() {
    const ap = new Autopilot(this);
    return Object.fromEntries(LANDMARKS.map((n) => [n, ap.landmark(n).map((v) => +v.toFixed(4))]));
  }
}
