// The draped patient: a CC-BY scanned human body + Blender-baked cloth drapes
// (scripts/blender/build_patient.py -> assets/or/patient.glb, already in OR coordinates),
// with PBR fabric (ambientCG CC0) assigned here by material name, plus the lines that
// connect the patient to the room: IV line, ECG leads, pulse-ox cable, warming hose.
import * as THREE from 'three';
import { gltfLoader } from './room.js';
import LM from './patient-landmarks.json';

/** OR-world height of the abdomen-frame origin so the trocars sit on the skin (see outside.js). */
export function abdomenOriginY(ports) {
  const r = Object.entries(LM.skinAtPorts).filter(([k, v]) => v && k !== 'umbilical').map(([k, v]) => v - ports[k].pos[2]);
  r.sort((a, b) => a - b);
  return r.length ? r[Math.floor(r.length / 2)] : 0.79;
}

const texLoader = new THREE.TextureLoader();
const tex = async (renderer, name, repeat) => {
  const t = await texLoader.loadAsync(`./assets/fabric/${name}.jpg`);
  t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(repeat, repeat); t.anisotropy = 4;
  return t;
};
function stainTexture() { // irregular painted iodine margin (alpha), fades at the edges
  const c = document.createElement('canvas'); c.width = c.height = 256; const x = c.getContext('2d');
  const g = x.createRadialGradient(128, 128, 30, 128, 128, 128); g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(0.75, 'rgba(255,255,255,0.85)'); g.addColorStop(1, 'rgba(255,255,255,0)');
  x.fillStyle = g; x.fillRect(0, 0, 256, 256);
  x.globalCompositeOperation = 'destination-out';
  for (let i = 0; i < 70; i++) { x.fillStyle = `rgba(0,0,0,${0.1 + Math.random() * 0.25})`; x.beginPath(); const a = Math.random() * 6.28, r = 90 + Math.random() * 40; x.ellipse(128 + Math.cos(a) * r, 128 + Math.sin(a) * r, 8 + Math.random() * 22, 6 + Math.random() * 14, a, 0, 7); x.fill(); }
  const t = new THREE.CanvasTexture(c); return t;
}

export async function loadPatient(renderer, parent) {
  const [{ scene }, dC, dN, dR, bC, bN] = await Promise.all([
    gltfLoader(renderer).loadAsync('./assets/or/patient.glb'),
    tex(renderer, 'Fabric081A_color', 1), tex(renderer, 'Fabric081A_normal', 1), tex(renderer, 'Fabric081A_roughness', 1),
    tex(renderer, 'Fabric008_color', 1), tex(renderer, 'Fabric008_normal', 1),
  ]);
  dC.colorSpace = bC.colorSpace = THREE.SRGBColorSpace;
  const drape = new THREE.MeshStandardMaterial({ map: dC, normalMap: dN, roughnessMap: dR, roughness: 1, color: 0xb8d0e6, normalScale: new THREE.Vector2(0.7, 0.7), side: THREE.DoubleSide });
  const M = {
    drape_fabric: drape,
    blanket_fabric: new THREE.MeshStandardMaterial({ map: bC, normalMap: bN, roughness: 0.85, color: 0xdbe7f2, side: THREE.DoubleSide }),
    strap: new THREE.MeshStandardMaterial({ color: 0x2d3540, roughness: 0.75 }),
    armboard_pad: new THREE.MeshStandardMaterial({ color: 0x1d2633, roughness: 0.45 }),
    armboard_metal: new THREE.MeshStandardMaterial({ color: 0xc9cdd1, roughness: 0.25, metalness: 1 }),
    bar_metal: new THREE.MeshStandardMaterial({ color: 0xc9cdd1, roughness: 0.25, metalness: 1 }),
    cap_fabric: new THREE.MeshStandardMaterial({ map: dC, normalMap: dN, color: 0x8fd0c8, roughness: 0.95 }),
    eyepad: new THREE.MeshStandardMaterial({ color: 0xf2f2ef, roughness: 0.9 }),
    tape: new THREE.MeshStandardMaterial({ color: 0xe8e2d6, roughness: 0.8 }),
    ettube: new THREE.MeshPhysicalMaterial({ color: 0xeef6ff, roughness: 0.15, transparent: true, opacity: 0.75 }),
    circuit: new THREE.MeshPhysicalMaterial({ color: 0x7fb6e8, roughness: 0.35, transparent: true, opacity: 0.85 }),
    pulseox: new THREE.MeshStandardMaterial({ color: 0x2a2f36, roughness: 0.5, emissive: 0x330000 }),
    prep_stain: new THREE.MeshStandardMaterial({ color: 0x8a3c12, alphaMap: stainTexture(), transparent: true, opacity: 0.78, roughness: 0.35, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -1 }),
    incise_film: new THREE.MeshPhysicalMaterial({ color: 0xfff6e6, transparent: true, opacity: 0.14, roughness: 0.04, clearcoat: 1, clearcoatRoughness: 0.03, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2 }),
  };
  scene.traverse((o) => {
    if (!o.isMesh) return;
    const name = o.material?.name;
    if (M[name]) o.material = M[name];
    if (name === 'drape_fabric' || name === 'blanket_fabric' || name === 'cap_fabric') planarUV(o, name === 'blanket_fabric' ? 0.22 : 0.3);
    if (name === 'prep_stain') windowUV(o, 0.07);
    o.castShadow = !['prep_stain', 'incise_film'].includes(name);
    o.receiveShadow = !['drape_fabric', 'blanket_fabric', 'strap', 'cap_fabric'].includes(name); // avoid shadow acne on cloth
    if (name === 'prep_stain' || name === 'incise_film') o.renderOrder = 2;
  });
  scene.name = 'patient-real';
  parent.add(scene);
  parent.add(lines());
  return scene;
}

/** World-space planar UVs for the baked cloth (texture repeats via the material). */
function planarUV(o, size) {
  o.updateWorldMatrix(true, false);
  const p = o.geometry.attributes.position, v = new THREE.Vector3(), uv = new Float32Array(p.count * 2);
  for (let i = 0; i < p.count; i++) { v.fromBufferAttribute(p, i).applyMatrix4(o.matrixWorld); uv[i * 2] = (v.x + 0.6 * v.y) / size; uv[i * 2 + 1] = (v.z + 0.6 * v.y) / size; }
  o.geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
}

/** UVs spanning the prepped area around the abdominal window (for the stain's alpha map). */
function windowUV(o, margin) {
  o.updateWorldMatrix(true, false);
  const w = LM.window, p = o.geometry.attributes.position, v = new THREE.Vector3(), uv = new Float32Array(p.count * 2);
  for (let i = 0; i < p.count; i++) { v.fromBufferAttribute(p, i).applyMatrix4(o.matrixWorld); uv[i * 2] = (v.x - w.x0 + margin) / (w.x1 - w.x0 + 2 * margin); uv[i * 2 + 1] = (v.z - w.z0 + margin) / (w.z1 - w.z0 + 2 * margin); }
  o.geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
}

function cable(points, r, color, opacity = 1) {
  const m = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p))), 48, r, 6),
    new THREE.MeshStandardMaterial({ color, roughness: 0.4, transparent: opacity < 1, opacity }));
  m.castShadow = true; return m;
}
function lines() {
  const g = new THREE.Group(); g.name = 'patient-lines';
  const fa = LM.forearmL, ft = LM.fingertipL, anes = [1.7, 1.15, -0.95];
  // IV line: bag on the IV stand -> drip chamber -> sagging line -> cannula taped on the left forearm
  const bag = [1.3, 1.62, -0.05];
  g.add(cable([bag, [1.3, 1.35, -0.06], [1.15, 0.95, -0.12], [fa[0] + 0.08, fa[1] + 0.06, fa[2] + 0.02], [fa[0], fa[1] + 0.012, fa[2]]], 0.0016, 0xf4f8ff, 0.7));
  const tape = new THREE.Mesh(new THREE.BoxGeometry(0.035, 0.002, 0.045), new THREE.MeshStandardMaterial({ color: 0xf5f0e6, roughness: 0.8 }));
  tape.position.set(fa[0], fa[1] + 0.013, fa[2]); g.add(tape);
  // pulse-ox cable from the clip on the left index finger to the anaesthesia machine
  g.add(cable([[ft[0], ft[1] + 0.01, ft[2]], [ft[0] + 0.1, ft[1] - 0.05, ft[2] + 0.08], [1.3, 0.7, -0.4], anes], 0.002, 0x2b2f36));
  // ECG leads (white / black / red) from under the drape at the anaesthesia screen to the monitor
  const leadCol = [0xf2f2f2, 0x1b1b1b, 0xc8202a];
  LM.chestLeads.forEach((p, i) => g.add(cable([p, [p[0] + 0.15, p[1] + 0.08, p[2] - 0.02], [1.5, 1.0, -0.85 + i * 0.03], [anes[0], anes[1] - 0.03 * i, anes[2] + 0.02 * i]], 0.0015, leadCol[i])));
  // forced-air warming: hose from the foot end of the blanket to the warming unit on the floor
  const fx = LM.bodyBounds.xmin - 0.08;
  g.add(cable([[fx, 0.82, -0.75], [fx - 0.18, 0.7, -0.7], [fx - 0.3, 0.38, -0.55], [fx - 0.35, 0.36, -0.42]], 0.035, 0xdfe5ea));
  const unit = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.36, 0.3), new THREE.MeshStandardMaterial({ color: 0xe9edf0, roughness: 0.4 }));
  unit.position.set(fx - 0.38, 0.18, -0.38); unit.castShadow = true; g.add(unit);
  const panel = new THREE.Mesh(new THREE.PlaneGeometry(0.12, 0.06), new THREE.MeshBasicMaterial({ color: 0x103040 }));
  panel.position.set(fx - 0.38, 0.3, -0.229); g.add(panel);
  return g;
}
