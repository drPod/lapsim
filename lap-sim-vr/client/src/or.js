// Procedural draped patient (abdominal window + trocar ports) lying on the OR table.
// The room, lighting and equipment live in room.js (Sketchfab CC-BY models).
//
// Coordinates: metres, local-floor. The surgeon stands at the origin facing -Z,
// on the patient's left. The patient lies along X with the head at +X.
import * as THREE from 'three';


const mat = {
  drape: new THREE.MeshStandardMaterial({ color: 0x3f7fb0, roughness: 0.95 }),
  drapeDark: new THREE.MeshStandardMaterial({ color: 0x2f6390, roughness: 0.95 }),
  steel: new THREE.MeshStandardMaterial({ color: 0xc8ccd0, roughness: 0.3, metalness: 0.9 }),
  darkSteel: new THREE.MeshStandardMaterial({ color: 0x4a5058, roughness: 0.4, metalness: 0.7 }),
  plastic: new THREE.MeshStandardMaterial({ color: 0xe9ecef, roughness: 0.5 }),
  black: new THREE.MeshStandardMaterial({ color: 0x1a1c20, roughness: 0.5 }),
  skin: new THREE.MeshStandardMaterial({ color: 0xd9a58a, roughness: 0.7 }),
  iodine: new THREE.MeshStandardMaterial({ color: 0x9a5a2a, roughness: 0.6 }),
  cap: new THREE.MeshStandardMaterial({ color: 0x8fb8d8, roughness: 0.95 }),
  bag: new THREE.MeshPhysicalMaterial({ color: 0xffffff, roughness: 0.15, transmission: 0.6, transparent: true, opacity: 0.6 }),
};

function box(w, h, d, m) { const g = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m); g.castShadow = g.receiveShadow = true; return g; }
function cyl(rt, rb, h, m, seg = 24) { const g = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg), m); g.castShadow = true; return g; }
function rounded(w, h, d, r, m) {
  const shape = new THREE.Shape();
  const x = -w / 2, y = -d / 2;
  shape.moveTo(x + r, y); shape.lineTo(x + w - r, y); shape.quadraticCurveTo(x + w, y, x + w, y + r);
  shape.lineTo(x + w, y + d - r); shape.quadraticCurveTo(x + w, y + d, x + w - r, y + d);
  shape.lineTo(x + r, y + d); shape.quadraticCurveTo(x, y + d, x, y + d - r); shape.lineTo(x, y + r); shape.quadraticCurveTo(x, y, x + r, y);
  const g = new THREE.ExtrudeGeometry(shape, { depth: h, bevelEnabled: true, bevelThickness: 0.02, bevelSize: 0.02, bevelSegments: 3 });
  g.rotateX(-Math.PI / 2); g.translate(0, 0, 0);
  const m2 = new THREE.Mesh(g, m); m2.castShadow = m2.receiveShadow = true; return m2;
}

export function buildPatient(surfaceY) {
  const g = new THREE.Group(); g.name = 'patient';
  // One cloth mesh draped over an implied body: a height field from a few
  // ellipsoids (chest, abdomen, pelvis, legs) that falls over the table edges.
  const L = 1.9, Wd = 1.1, nx = 190, nz = 110;
  const geo = new THREE.PlaneGeometry(L, Wd, nx, nz); geo.rotateX(-Math.PI / 2); geo.translate(0.68 - L / 2, 0, 0); // ends at the ether screen
  const blobs = [ // x, z, rx, rz, height
    [0.35, 0, 0.32, 0.24, 0.15], [0.05, 0, 0.32, 0.23, 0.14], [-0.3, 0, 0.24, 0.22, 0.11],
    [-0.75, 0.09, 0.42, 0.085, 0.075], [-0.75, -0.09, 0.42, 0.085, 0.075],
  ];
  const win = { x: 0.14, z: 0.02, rx: 0.2, rz: 0.13 }; // prepped abdominal window
  const pos = geo.attributes.position, colors = [];
  const blue = new THREE.Color(0x173f5e), iod = new THREE.Color(0x7a4026), tmp = new THREE.Color();
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = pos.getZ(i);
    let h = 0;
    for (const [bx, bz, rx, rz, bh] of blobs) {
      const q = ((x - bx) / rx) ** 2 + ((z - bz) / rz) ** 2;
      if (q < 1) h = Math.max(h, bh * Math.sqrt(1 - q));
    }
    const az = Math.abs(z), ax = x < -1.0 ? -1.0 - x : 0;
    let y = surfaceY + 0.015 + h;
    if (az > 0.29) y -= (az - 0.29) * 2.4;            // hangs over the long edges
    if (ax > 0) y -= ax * 2.6;                        // and over the foot end
    y += 0.004 * Math.sin(x * 23 + z * 7) * Math.sin(z * 19) + 0.0025 * Math.sin(x * 61 - z * 13); // fabric folds
    pos.setY(i, y);
    const qw = ((x - win.x) / win.rx) ** 2 + ((z - win.z) / win.rz) ** 2;
    tmp.copy(blue).multiplyScalar(0.92 + 0.08 * Math.sin(x * 40) * Math.sin(z * 30));
    tmp.lerp(iod, THREE.MathUtils.smoothstep(1.0 - qw, 0.0, 0.25)); // soft-edged iodine-prepped skin
    colors.push(tmp.r, tmp.g, tmp.b);
  }
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geo.computeVertexNormals();
  const drape = new THREE.Mesh(geo, new THREE.MeshPhysicalMaterial({ vertexColors: true, roughness: 0.95, sheen: 0.35, sheenRoughness: 0.8, sheenColor: new THREE.Color(0x6fa8d0), side: THREE.DoubleSide }));
  drape.castShadow = true;
  drape.receiveShadow = true; drape.name = 'drape';
  g.add(drape);
  const heightAt = (x, z) => { let h = 0; for (const [bx, bz, rx, rz, bh] of blobs) { const q = ((x - bx) / rx) ** 2 + ((z - bz) / rz) ** 2; if (q < 1) h = Math.max(h, bh * Math.sqrt(1 - q)); } return surfaceY + 0.015 + h; };
  // patient-local: +X = head, +Z = patient's left (surgeon side). Names match abdomen/tools.js PORTS.
  const ports = { umbilical: [0.0, 0.01], epigastric: [0.26, 0.01], rsc: [0.18, -0.09], raa: [0.1, -0.16] };
  g.userData.ports = {};
  for (const [name, [x, z]] of Object.entries(ports)) {
    const p = new THREE.Group();
    const cannula = cyl(0.008, 0.008, 0.12, mat.darkSteel, 12); cannula.position.y = 0.06;
    const hub = cyl(0.018, 0.016, 0.035, mat.black, 16); hub.position.y = 0.12;
    p.add(cannula, hub); p.position.set(x, heightAt(x, z) - 0.01, z); p.rotation.z = (x - 0.15) * 0.8;
    g.add(p); g.userData.ports[name] = p;
  }
  // head end: ether screen (anaesthesia drape bar), head with cap on a pillow
  const bar = new THREE.Mesh(new THREE.TorusGeometry(0.42, 0.008, 8, 32, Math.PI), mat.steel);
  bar.rotation.y = Math.PI / 2; bar.position.set(0.66, surfaceY + 0.02, 0);
  const screen = new THREE.Mesh(new THREE.CircleGeometry(0.42, 32, 0, Math.PI), new THREE.MeshStandardMaterial({ color: 0x3f7fb0, roughness: 1, side: THREE.DoubleSide }));
  screen.rotation.y = Math.PI / 2; screen.position.set(0.66, surfaceY + 0.02, 0);
  const pillow = rounded(0.32, 0.06, 0.4, 0.06, mat.plastic); pillow.position.set(0.85, surfaceY, 0);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.1, 24, 16), mat.skin); head.scale.set(1.1, 0.9, 0.9); head.position.set(0.86, surfaceY + 0.14, 0);
  const cap = new THREE.Mesh(new THREE.SphereGeometry(0.105, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), mat.cap);
  cap.rotation.z = -Math.PI / 2; cap.position.copy(head.position).add(new THREE.Vector3(0.02, 0, 0));
  const tube = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([
    new THREE.Vector3(0.82, surfaceY + 0.2, 0), new THREE.Vector3(0.9, surfaceY + 0.35, 0.05), new THREE.Vector3(1.1, surfaceY + 0.4, -0.2), new THREE.Vector3(1.35, surfaceY + 0.2, -0.45)]), 32, 0.012, 8),
  new THREE.MeshStandardMaterial({ color: 0xbfd8e8, roughness: 0.2, transparent: true, opacity: 0.8 }));
  g.add(bar, screen, pillow, head, cap, tube);
  return g;
}

