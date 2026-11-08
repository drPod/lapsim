// The instruments as seen in the OR: long laparoscopic instruments entering the draped
// patient through trocars, pivoting at the trocar, driven by exactly the same fulcrum
// poses as the virtual abdomen (tracker, controllers, keyboard or autopilot).
//
// FRAME MAPPING (one place to change it later):
// The abdomen frame (client/src/abdomen/tools.js PORTS, metres) is placed RIGIDLY (no
// mirroring) on the patient so that, for the surgeon standing at the origin facing -Z:
//   abdomen +x (screen right on the lap monitor) -> world +X  (surgeon's right)
//   abdomen +z (anterior, out of the belly)      -> world +Y  (up)
//   abdomen +y ("head" on the lap monitor)       -> world -Z  (away from the surgeon)
// so a stick moved left/right in the box moves left/right in the room AND on the monitor.
// Known anatomical inconsistency (to fix in one go later, together with the abdomen's
// left/right mirror): the abdomen's head axis points across the table, not along the
// placeholder patient's body axis (+X), and rsc/raa sit on the surgeon's side.
import * as THREE from 'three';
import { PORTS, poseToShaft, hingeToPose } from './abdomen/tools.js';

export function abdomenToWorld(origin) {
  return new THREE.Matrix4().makeBasis(new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, -1), new THREE.Vector3(0, 1, 0)).setPosition(origin);
}

const black = new THREE.MeshStandardMaterial({ color: 0x16181b, roughness: 0.35, metalness: 0.2 });
const steel = new THREE.MeshStandardMaterial({ color: 0xc9cdd1, roughness: 0.25, metalness: 1 });
const grey = new THREE.MeshStandardMaterial({ color: 0x3c4148, roughness: 0.5 });
const blue = new THREE.MeshStandardMaterial({ color: 0x2b6cb0, roughness: 0.45 });
const LENGTH = 0.36; // working length of a 5 mm instrument (m)

function cylZ(r, len, m, z0) { const c = new THREE.Mesh(new THREE.CylinderGeometry(r, r, len, 16), m); c.rotation.x = Math.PI / 2; c.position.z = z0 + len / 2; return c; }

/** Local frame: origin = jaw hinge (distal end), shaft along +Z towards the surgeon's hand, +Y = jaw "up". */
function makeLongInstrument(type) {
  const g = new THREE.Group(); g.name = `outside-${type}`;
  if (type === 'scope') {
    g.add(cylZ(0.005, LENGTH, steel, 0));
    g.add(cylZ(0.016, 0.07, grey, LENGTH));                 // camera head
    const cable = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([new THREE.Vector3(0, 0, LENGTH + 0.07), new THREE.Vector3(0, -0.05, LENGTH + 0.2), new THREE.Vector3(0, -0.25, LENGTH + 0.35)]), 16, 0.004, 6), black);
    const light = cylZ(0.004, 0.03, steel, LENGTH - 0.05); light.position.y = 0.012; light.rotation.z = 0;
    g.add(cable, light);
  } else {
    g.add(cylZ(0.0025, LENGTH, black, 0));
    g.add(cylZ(0.006, 0.02, grey, LENGTH));                  // rotation knob
    const grip = new THREE.Mesh(new THREE.BoxGeometry(0.018, 0.09, 0.028), type === 'hook' ? blue : grey); // pistol grip
    grip.position.set(0, -0.05, LENGTH + 0.03); grip.rotation.x = 0.35; g.add(grip);
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.018, 0.005, 8, 20), grey); // finger ring (moving handle)
    ring.position.set(0, -0.06, LENGTH + 0.065); ring.rotation.y = Math.PI / 2; g.add(ring); g.userData.ring = ring;
    if (type === 'hook') { const c = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([new THREE.Vector3(0, 0, LENGTH + 0.02), new THREE.Vector3(0, 0.04, LENGTH + 0.12), new THREE.Vector3(0, -0.1, LENGTH + 0.3)]), 12, 0.003, 6), blue); g.add(c); }
  }
  g.traverse((o) => { if (o.isMesh) o.castShadow = true; });
  g.matrixAutoUpdate = false;
  return g;
}

function makeTrocar(big) {
  const g = new THREE.Group();
  const r = big ? 0.0075 : 0.0055;
  g.add(cylZ(r, 0.11, steel, -0.06));                         // cannula through the abdominal wall
  g.add(cylZ(r * 2.4, 0.03, black, 0.05));                    // valve housing (outside)
  const tap = new THREE.Mesh(new THREE.CylinderGeometry(0.003, 0.003, 0.025, 8), blue); tap.position.set(r * 2.4 + 0.008, 0, 0.065); tap.rotation.z = Math.PI / 2; g.add(tap); // insufflation tap
  g.matrixAutoUpdate = false;
  return g;
}

export class OutsideInstruments {
  /** @param parent group to add to (the OR world) @param M abdomen -> parent matrix */
  constructor(parent, M) {
    this.parent = parent; this.M = M; this.R = new THREE.Matrix3().setFromMatrix4(M);
    this.Minv = M.clone().invert(); this.Rinv = this.R.clone().invert();
    this.trocars = {}; this.inst = {};
    for (const name of Object.keys(PORTS)) { const t = makeTrocar(name === 'umbilical' || name === 'epigastric'); parent.add(t); this.trocars[name] = t; }
  }
  /** World position of a port's fulcrum (for the controller-as-instrument mode). */
  portWorld(port, out = new THREE.Vector3()) { return out.fromArray(PORTS[port].pos).applyMatrix4(this.M); }
  /** Controller handle at world point H -> abdomen-frame fulcrum pose through `port` (exact inverse of update()). */
  poseFromHandle(port, H) {
    const P = this.portWorld(port), dirW = P.clone().sub(H), dist = dirW.length(); dirW.normalize();
    const dirA = dirW.applyMatrix3(this.Rinv).normalize();
    const depth = THREE.MathUtils.clamp(LENGTH - dist, 0.02, 0.2);
    const hinge = new THREE.Vector3().fromArray(PORTS[port].pos).addScaledVector(dirA, depth);
    return { ...hingeToPose(port, hinge.toArray()), depth };
  }
  update(poses) {
    const seen = new Set();
    for (const [id, pose] of Object.entries(poses)) {
      if (!pose?.port || !PORTS[pose.port]) continue;
      seen.add(id);
      const type = pose.tool === 'scope' ? 'scope' : pose.tool;
      let g = this.inst[id];
      if (!g || g.userData.type !== type) { if (g) this.parent.remove(g); g = makeLongInstrument(type); g.userData.type = type; this.parent.add(g); this.inst[id] = g; }
      const s = poseToShaft(pose);
      const hinge = new THREE.Vector3(...s.hinge).applyMatrix4(this.M);
      const dir = new THREE.Vector3(...s.dir).applyMatrix3(this.R).normalize();
      const up = new THREE.Vector3(...s.up).applyMatrix3(this.R).normalize();
      const z = dir.clone().negate(), x = new THREE.Vector3().crossVectors(up, z).normalize(), y = new THREE.Vector3().crossVectors(z, x);
      g.matrix.makeBasis(x, y, z).setPosition(hinge); g.matrixWorldNeedsUpdate = true;
      if (g.userData.ring) g.userData.ring.position.z = LENGTH + 0.065 - 0.015 * (1 - (pose.jaw ?? 1)); // squeeze = jaws closed
      // the trocar pivots with its instrument
      const t = this.trocars[pose.port], P = this.portWorld(pose.port);
      t.matrix.makeBasis(x, y, z).setPosition(P); t.matrixWorldNeedsUpdate = true;
    }
    for (const [id, g] of Object.entries(this.inst)) if (!seen.has(id)) { this.parent.remove(g); delete this.inst[id]; }
    // idle trocars point along the port's nominal axis
    for (const [name, t] of Object.entries(this.trocars)) {
      if (Object.values(poses).some((p) => p?.port === name)) continue;
      const P = this.portWorld(name), d = new THREE.Vector3(...PORTS[name].aim).sub(new THREE.Vector3(...PORTS[name].pos)).applyMatrix3(this.R).normalize();
      const z = d.negate(), x = new THREE.Vector3(1, 0, 0).cross(z).lengthSq() > 1e-6 ? new THREE.Vector3(0, 1, 0).cross(z).normalize() : new THREE.Vector3(1, 0, 0), y = new THREE.Vector3().crossVectors(z, x);
      t.matrix.makeBasis(x, y, z).setPosition(P); t.matrixWorldNeedsUpdate = true;
    }
  }
}
