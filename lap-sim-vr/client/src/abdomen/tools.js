// Laparoscopic instruments + the fulcrum (trocar) kinematics shared by every
// pose source: the physical tracker, the keyboard/mouse, the VR controllers and
// the scripted autopilot all produce the same per-tool pose:
//   { id, port, tool, yaw, pitch, roll, depth, jaw, energy }
// yaw/pitch (rad) rotate the shaft about the port's nominal axis, roll (rad) spins
// it about its own axis, depth (m) = trocar pivot -> jaw hinge, jaw 0 = closed .. 1 = open.
// This module has no DOM dependencies (also used by scripts/sim-tools.mjs).

/** Port positions + nominal axes in the abdomen frame (metres). `aim` is where the
 * instrument points at yaw = pitch = 0. Match these to the physical box lid. */
export const PORTS = {
  umbilical: { pos: [0.01, -0.085, 0.1], aim: [-0.01, -0.004, -0.004], label: 'Umbilical (10 mm, laparoscope)' },
  epigastric: { pos: [-0.075, 0.07, 0.11], aim: [-0.01, -0.012, 0.0], label: 'Epigastric (10 mm, surgeon right hand)' },
  rsc: { pos: [0.085, -0.05, 0.11], aim: [0.005, -0.004, 0.0], label: 'Right subcostal (5 mm, surgeon left hand)' },
  raa: { pos: [0.12, 0.06, 0.085], aim: [0.03, 0.04, 0.0], label: 'Right anterior axillary (5 mm, assistant)' },
};
export const TOOL_TYPES = ['grasper', 'maryland', 'hook', 'scissors', 'clipper'];
export const DEFAULT_TOOLS = {
  scope: { port: 'umbilical', tool: 'scope', yaw: 0, pitch: 0, roll: 0, depth: 0.065, jaw: 1 },
  // instruments start just inside their trocars, clear of tissue
  left: { port: 'rsc', tool: 'grasper', yaw: 0, pitch: 0, roll: 0, depth: 0.05, jaw: 1 },
  right: { port: 'epigastric', tool: 'maryland', yaw: 0, pitch: 0, roll: 0, depth: 0.05, jaw: 1 },
  assist: { port: 'raa', tool: 'grasper', yaw: 0, pitch: 0, roll: 0, depth: 0.05, jaw: 1 },
};

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => scale(a, 1 / Math.hypot(...a));

/** Orthonormal port frame: d = nominal insertion axis, r = right, u = up. */
export function portFrame(port) {
  const P = typeof port === 'string' ? PORTS[port] : port;
  const d = norm(sub(P.aim, P.pos));
  let r = cross(d, [0, 1, 0]); if (Math.hypot(...r) < 1e-6) r = [1, 0, 0];
  r = norm(r); const u = cross(r, d);
  return { o: P.pos, d, r, u };
}

/** Forward kinematics: pose -> shaft direction, hinge (distal end) and roll-aware up vector. */
export function poseToShaft(pose) {
  const F = portFrame(pose.port);
  const cy = Math.cos(pose.yaw), sy = Math.sin(pose.yaw), cp = Math.cos(pose.pitch), sp = Math.sin(pose.pitch);
  const dir = norm(add(add(scale(F.d, cp * cy), scale(F.r, cp * sy)), scale(F.u, sp)));
  // up: port up re-orthogonalised to the shaft, then rolled about it
  let up = norm(sub(F.u, scale(dir, dot(F.u, dir))));
  const side = cross(dir, up), cr = Math.cos(pose.roll || 0), sr = Math.sin(pose.roll || 0);
  up = add(scale(up, cr), scale(side, sr));
  const hinge = add(F.o, scale(dir, pose.depth));
  return { origin: F.o, dir, up, hinge, tip: add(hinge, scale(dir, 0.008)) };
}

/** Inverse kinematics: where should the hinge be -> yaw/pitch/depth (used by autopilot & controllers). */
export function hingeToPose(port, hinge) {
  const F = portFrame(port);
  const v = sub(hinge, F.o), depth = Math.hypot(...v), dir = scale(v, 1 / depth);
  const pitch = Math.asin(Math.max(-1, Math.min(1, dot(dir, F.u))));
  const yaw = Math.atan2(dot(dir, F.r), dot(dir, F.d));
  return { yaw, pitch, depth };
}
/** IK for the interaction point (8 mm beyond the hinge). */
export function tipToPose(port, tip) {
  const F = portFrame(port);
  const v = sub(tip, F.o), L = Math.hypot(...v);
  return hingeToPose(port, add(F.o, scale(v, (L - 0.008) / L)));
}

// ------------------------------------------------------------------ meshes (browser only)
export function makeInstrument(THREE, type) {
  const g = new THREE.Group(); g.name = `tool-${type}`;
  const steel = new THREE.MeshPhysicalMaterial({ color: 0xd8dadc, metalness: 1, roughness: 0.22, clearcoat: 0.3 });
  const dark = new THREE.MeshPhysicalMaterial({ color: 0x15171a, roughness: 0.38, metalness: 0.1, clearcoat: 0.6, clearcoatRoughness: 0.2 });
  const cyl = (r, l, m, seg = 20) => { const c = new THREE.Mesh(new THREE.CylinderGeometry(r, r, l, seg), m); c.rotation.x = Math.PI / 2; return c; };
  // local frame: hinge at origin, shaft toward +Z (out of the body), jaws toward -Z
  const R = type === 'scope' ? 0.005 : 0.0025;
  const shaft = cyl(R, 0.42, type === 'scope' ? steel : dark); shaft.position.z = 0.215; g.add(shaft);
  const collar = cyl(R * 1.04, 0.006, steel); collar.position.z = 0.004; g.add(collar);
  g.userData.jaws = [];
  const jaw = (curve, width, len, thick) => {
    const pivot = new THREE.Group();
    const shape = new THREE.Shape(); // jaw side profile in (z, y): tapered
    shape.moveTo(0, 0); shape.lineTo(-len, -thick * 0.15); shape.lineTo(-len, thick * 0.35); shape.lineTo(0, thick); shape.lineTo(0, 0);
    const geo = new THREE.ExtrudeGeometry(shape, { depth: width, bevelEnabled: true, bevelThickness: 0.0002, bevelSize: 0.0002, bevelSegments: 1, steps: 1 });
    geo.translate(0, 0, -width / 2); geo.rotateY(-Math.PI / 2); // jaw length -> -Z, width -> x
    // curve the jaw sideways (Maryland / Metzenbaum)
    if (curve) { const p = geo.attributes.position; for (let i = 0; i < p.count; i++) { const z = p.getZ(i); p.setX(i, p.getX(i) + curve * z * z * 60); } geo.computeVertexNormals(); }
    const m = new THREE.Mesh(geo, steel); pivot.add(m);
    return pivot;
  };
  if (type === 'grasper' || type === 'maryland' || type === 'scissors' || type === 'clipper') {
    const spec = { grasper: [0, 0.0022, 0.016, 0.0016], maryland: [0.5, 0.0018, 0.015, 0.0015], scissors: [0.35, 0.0012, 0.014, 0.0012], clipper: [0, 0.0028, 0.012, 0.0016] }[type];
    const a = jaw(spec[0], spec[1], spec[2], spec[3]), b = jaw(spec[0], spec[1], spec[2], spec[3]);
    b.scale.y = -1;
    g.add(a, b); g.userData.jaws = [a, b];
    g.userData.maxOpen = type === 'scissors' ? 0.38 : 0.45;
    if (type === 'clipper') { const clip = makeClip(THREE); clip.position.z = -0.002; clip.rotation.y = Math.PI / 2; g.add(clip); g.userData.loaded = clip; }
  } else if (type === 'hook') {
    const sheath = cyl(0.0012, 0.01, dark, 12); sheath.position.z = -0.003; g.add(sheath);
    const wire = cyl(0.0005, 0.012, steel, 10); wire.position.z = -0.012; g.add(wire);
    const bend = new THREE.Mesh(new THREE.TorusGeometry(0.0016, 0.0005, 8, 16, Math.PI), steel);
    bend.rotation.y = Math.PI / 2; bend.position.set(0, 0.0016, -0.018); g.add(bend);
    const back = cyl(0.0005, 0.003, steel, 10); back.position.set(0, 0.0032, -0.0165); g.add(back);
    const glow = new THREE.PointLight(0xffaa55, 0, 0.02, 2); glow.position.set(0, 0.002, -0.019); g.add(glow); g.userData.glow = glow;
  } else if (type === 'scope') {
    const lens = new THREE.Mesh(new THREE.CircleGeometry(0.004, 24), new THREE.MeshBasicMaterial({ color: 0x223344 }));
    lens.rotation.y = Math.PI; g.add(lens);
  }
  g.traverse((o) => { o.frustumCulled = false; });
  return g;
}

export function makeClip(THREE) {
  const ti = new THREE.MeshPhysicalMaterial({ color: 0xc9ccd2, metalness: 1, roughness: 0.18, clearcoat: 0.5 });
  const g = new THREE.Group(); g.name = 'clip';
  const leg = (y) => { const m = new THREE.Mesh(new THREE.BoxGeometry(0.009, 0.0007, 0.0012), ti); m.position.set(0.0045, y, 0); return m; };
  const crown = new THREE.Mesh(new THREE.TorusGeometry(0.0009, 0.00035, 6, 12, Math.PI), ti); crown.rotation.z = Math.PI / 2;
  g.add(leg(0.0009), leg(-0.0009), crown);
  return g;
}

/** Place a tool group from a pose (three.js objects). */
export function applyPoseToGroup(THREE, group, pose, t = 0) {
  const s = poseToShaft(pose);
  const z = new THREE.Vector3(...s.dir).negate(), y = new THREE.Vector3(...s.up), x = new THREE.Vector3().crossVectors(y, z);
  group.matrix.makeBasis(x, y, z).setPosition(...s.hinge);
  group.matrixAutoUpdate = false; group.matrixWorldNeedsUpdate = true;
  const open = (group.userData.maxOpen || 0.5) * Math.max(0, Math.min(1, pose.jaw ?? 1));
  const [a, b] = group.userData.jaws;
  if (a) { a.rotation.x = open; b.rotation.x = -open; }
  if (group.userData.loaded) group.userData.loaded.visible = (pose.jaw ?? 1) > 0.3;
  if (group.userData.glow) group.userData.glow.intensity = pose.energy ? 0.02 * (0.6 + 0.4 * Math.sin(t * 90)) : 0;
  return s;
}
