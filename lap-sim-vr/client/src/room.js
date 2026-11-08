// Modern operating room shell + lighting + the Sketchfab props (see assets-src/or-models.json).
// Coordinates: metres, local-floor; the surgeon stands at the origin facing -Z on the
// patient's left; the table runs along X (head at +X) centred at TABLE_POS.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { HDRLoader } from 'three/addons/loaders/HDRLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

export const ROOM = { x0: -3.4, x1: 3.8, z0: -3.9, z1: 2.3, h: 3.0 };
export const TABLE_POS = new THREE.Vector3(0.1, 0, -0.75);

// ------------------------------------------------------------------ procedural textures
function canvasTex(w, h, draw, { repeat = [1, 1], srgb = true } = {}) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(...repeat);
  t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  t.anisotropy = 8;
  return t;
}
function speckle(ctx, w, h, n, colors, rmin, rmax) {
  for (let i = 0; i < n; i++) {
    ctx.fillStyle = colors[i % colors.length];
    const r = rmin + Math.random() * (rmax - rmin);
    ctx.beginPath(); ctx.arc(Math.random() * w, Math.random() * h, r, 0, Math.PI * 2); ctx.fill();
  }
}
/** Seamless sheet-vinyl / epoxy floor: pale grey-green with fine flecks. */
function floorMaterial() {
  const map = canvasTex(1024, 1024, (x, w, h) => {
    x.fillStyle = '#c9d2cf'; x.fillRect(0, 0, w, h);
    for (let i = 0; i < 40; i++) { const g = x.createRadialGradient(Math.random() * w, Math.random() * h, 0, Math.random() * w, Math.random() * h, 200 + Math.random() * 300); g.addColorStop(0, 'rgba(255,255,255,0.05)'); g.addColorStop(1, 'rgba(0,0,0,0)'); x.fillStyle = g; x.fillRect(0, 0, w, h); }
    speckle(x, w, h, 9000, ['#e9eeec', '#9aa6a3', '#b6c1bd', '#7f8c88'], 0.6, 1.8);
  }, { repeat: [4, 4] });
  const rough = canvasTex(512, 512, (x, w, h) => {
    x.fillStyle = '#5a5a5a'; x.fillRect(0, 0, w, h);
    for (let i = 0; i < 60; i++) { x.strokeStyle = `rgba(${Math.random() < 0.5 ? '90,90,90' : '140,140,140'},0.25)`; x.lineWidth = 1 + Math.random() * 3; x.beginPath(); const y = Math.random() * h; x.moveTo(0, y); x.bezierCurveTo(w * 0.3, y + 40 * Math.random(), w * 0.6, y - 40 * Math.random(), w, y); x.stroke(); }
  }, { repeat: [3, 3], srgb: false });
  return new THREE.MeshStandardMaterial({ map, roughnessMap: rough, roughness: 0.55, metalness: 0, envMapIntensity: 0.9 });
}
/** Glossy laminate wall panels with seams. */
function wallMaterial(width) {
  const map = canvasTex(1024, 1024, (x, w, h) => {
    x.fillStyle = '#eef1f2'; x.fillRect(0, 0, w, h);
    x.fillStyle = '#c9d0d4'; x.fillRect(0, 0, 3, h); x.fillRect(0, h * 0.5, w, 3);
    speckle(x, w, h, 1500, ['rgba(0,0,0,0.025)', 'rgba(255,255,255,0.04)'], 1, 3);
  }, { repeat: [width / 1.25, 1.2] });
  return new THREE.MeshStandardMaterial({ map, roughness: 0.22, metalness: 0, envMapIntensity: 0.7 });
}
function ceilingMaterial(width, depth) {
  const map = canvasTex(512, 512, (x, w, h) => {
    x.fillStyle = '#f2f4f5'; x.fillRect(0, 0, w, h);
    x.fillStyle = '#d4d9dc'; x.fillRect(0, 0, w, 6); x.fillRect(0, 0, 6, h);
  }, { repeat: [width / 0.6, depth / 0.6] });
  return new THREE.MeshStandardMaterial({ map, roughness: 0.8 });
}
const blobTex = (() => { let t; return () => t ??= canvasTex(128, 128, (x, w) => { const g = x.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2); g.addColorStop(0, 'rgba(0,0,0,0.55)'); g.addColorStop(0.6, 'rgba(0,0,0,0.25)'); g.addColorStop(1, 'rgba(0,0,0,0)'); x.fillStyle = g; x.fillRect(0, 0, w, w); }); })();
/** Soft contact shadow ("baked AO" blob) under an object. */
export function contactShadow(w, d, opacity = 0.6) {
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshBasicMaterial({ map: blobTex(), transparent: true, opacity, depthWrite: false, toneMapped: false }));
  m.rotation.x = -Math.PI / 2; m.position.y = 0.004; m.renderOrder = 1; m.name = 'contact-shadow';
  return m;
}

// ------------------------------------------------------------------ room shell
export function buildRoom() {
  const g = new THREE.Group(); g.name = 'room';
  const { x0, x1, z0, z1, h } = ROOM, W = x1 - x0, D = z1 - z0, cx = (x0 + x1) / 2, cz = (z0 + z1) / 2;
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(W, D), floorMaterial());
  floor.rotation.x = -Math.PI / 2; floor.position.set(cx, 0, cz); floor.receiveShadow = true; floor.name = 'floor';
  g.add(floor);
  const steel = new THREE.MeshStandardMaterial({ color: 0xc8cdd0, metalness: 1, roughness: 0.3 });
  const rail = new THREE.MeshStandardMaterial({ color: 0xd9dee1, roughness: 0.35 });
  const cove = new THREE.MeshStandardMaterial({ color: 0x9aa7a3, roughness: 0.45 });
  const walls = [
    { w: W, pos: [cx, h / 2, z0], ry: 0 }, { w: W, pos: [cx, h / 2, z1], ry: Math.PI },
    { w: D, pos: [x0, h / 2, cz], ry: Math.PI / 2 }, { w: D, pos: [x1, h / 2, cz], ry: -Math.PI / 2 },
  ];
  for (const wl of walls) {
    const wall = new THREE.Mesh(new THREE.PlaneGeometry(wl.w, h), wallMaterial(wl.w));
    wall.position.set(...wl.pos); wall.rotation.y = wl.ry; wall.receiveShadow = true; g.add(wall);
    const grp = new THREE.Group(); grp.position.set(wl.pos[0], 0, wl.pos[2]); grp.rotation.y = wl.ry; g.add(grp);
    const kick = new THREE.Mesh(new THREE.BoxGeometry(wl.w, 0.12, 0.012), steel); kick.position.set(0, 0.06, 0.006); grp.add(kick); // stainless kick plate
    const coveM = new THREE.Mesh(new THREE.BoxGeometry(wl.w, 0.02, 0.05), cove); coveM.position.set(0, 0.01, 0.025); grp.add(coveM);
    const bump = new THREE.Mesh(new THREE.BoxGeometry(wl.w, 0.12, 0.03), rail); bump.position.set(0, 0.95, 0.015); grp.add(bump); // bumper rail
  }
  // ceiling grid + laminar-flow canopy over the table + LED panels
  const ceil = new THREE.Mesh(new THREE.PlaneGeometry(W, D), ceilingMaterial(W, D));
  ceil.rotation.x = Math.PI / 2; ceil.position.set(cx, h, cz); g.add(ceil);
  const canopyMat = new THREE.MeshStandardMaterial({
    color: 0xffffff, roughness: 0.6, emissive: 0xf4f8ff, emissiveIntensity: 0.9,
    emissiveMap: canvasTex(512, 512, (x, w, hh) => { x.fillStyle = '#9aa0a6'; x.fillRect(0, 0, w, hh); x.fillStyle = '#ffffff'; for (let yy = 6; yy < hh; yy += 10) for (let xx = 6; xx < w; xx += 10) { x.beginPath(); x.arc(xx, yy, 3, 0, 7); x.fill(); } }, { repeat: [3, 3] }),
  });
  const canopy = new THREE.Mesh(new THREE.BoxGeometry(3.0, 0.08, 2.6), [rail, rail, rail, canopyMat, rail, rail]);
  canopy.position.set(TABLE_POS.x, h - 0.04, TABLE_POS.z); canopy.name = 'laminar-flow-canopy'; g.add(canopy);
  const ledMat = new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0xffffff, emissiveIntensity: 1.6 });
  for (const [x, z] of [[-2.2, -2.8], [2.6, -2.8], [-2.2, 1.4], [2.6, 1.4], [-2.4, -0.8], [2.9, -0.8]]) {
    const p = new THREE.Mesh(new THREE.BoxGeometry(1.18, 0.02, 0.58), ledMat); p.position.set(x, h - 0.01, z); g.add(p);
  }
  for (const s of [-1, 1]) { const strip = new THREE.Mesh(new THREE.BoxGeometry(3.0, 0.03, 0.06), ledMat); strip.position.set(TABLE_POS.x, h - 0.1, TABLE_POS.z + s * 1.33); g.add(strip); }

  // hermetic sliding door (back wall, behind the anaesthesia side)
  const door = new THREE.Group(); door.position.set(2.2, 0, z0 + 0.03); door.name = 'door';
  const frame = new THREE.Mesh(new THREE.BoxGeometry(1.7, 2.25, 0.04), steel); frame.position.y = 1.125; door.add(frame);
  const leaf = new THREE.Mesh(new THREE.BoxGeometry(1.55, 2.15, 0.05), new THREE.MeshStandardMaterial({ color: 0xe4e9ec, roughness: 0.3 })); leaf.position.set(0, 1.09, 0.03); door.add(leaf);
  const win = new THREE.Mesh(new THREE.PlaneGeometry(0.42, 0.42), new THREE.MeshPhysicalMaterial({ color: 0x1a2a33, roughness: 0.05, metalness: 0.2, clearcoat: 1 })); win.position.set(0, 1.5, 0.056); door.add(win);
  const handle = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.6, 0.04), steel); handle.position.set(-0.6, 1.05, 0.08); door.add(handle);
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.14), new THREE.MeshBasicMaterial({ map: canvasTex(512, 144, (x) => { x.fillStyle = '#c4141c'; x.fillRect(0, 0, 512, 144); x.fillStyle = '#fff'; x.font = '700 64px system-ui'; x.textAlign = 'center'; x.fillText('OR 3 · IN USE', 256, 96); }) }));
  sign.position.set(0, 2.42, 0.01); door.add(sign);
  g.add(door);
  // wall display (PACS) + medical gas panel + clock on the left wall
  const pacs = new THREE.Mesh(new THREE.PlaneGeometry(1.3, 0.75), new THREE.MeshBasicMaterial({ map: canvasTex(1024, 590, (x, w, hh) => {
    x.fillStyle = '#05080b'; x.fillRect(0, 0, w, hh); x.fillStyle = '#0d1a24'; x.fillRect(0, 0, w, 44); x.fillStyle = '#7fb8ff'; x.font = '600 26px system-ui'; x.fillText('PACS · CT ABDOMEN · DOE, J · 58y', 18, 30);
    for (let i = 0; i < 3; i++) { const g2 = x.createRadialGradient(180 + i * 330, 320, 10, 180 + i * 330, 320, 150); g2.addColorStop(0, '#9a9a9a'); g2.addColorStop(0.6, '#3a3a3a'); g2.addColorStop(1, '#050505'); x.fillStyle = g2; x.beginPath(); x.ellipse(180 + i * 330, 320, 150, 120, 0, 0, 7); x.fill(); }
  }), toneMapped: false }));
  pacs.position.set(x0 + 0.02, 1.65, -1.4); pacs.rotation.y = Math.PI / 2; g.add(pacs);
  const gas = new THREE.Group(); gas.position.set(x1 - 0.02, 1.35, -1.9); gas.rotation.y = -Math.PI / 2;
  gas.add(new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.32, 0.04), new THREE.MeshStandardMaterial({ color: 0xe8ecee, roughness: 0.4 })));
  ['#2c7be5', '#ffffff', '#f2c94c', '#27ae60', '#111111'].forEach((c, i) => { const o = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.022, 0.03, 16), new THREE.MeshStandardMaterial({ color: c, roughness: 0.4 })); o.rotation.x = Math.PI / 2; o.position.set(-0.18 + i * 0.09, -0.04, 0.03); gas.add(o); });
  g.add(gas);
  const clock = new THREE.Mesh(new THREE.PlaneGeometry(0.6, 0.18), new THREE.MeshBasicMaterial({ map: canvasTex(512, 154, (x) => { x.fillStyle = '#070707'; x.fillRect(0, 0, 512, 154); x.fillStyle = '#ff3b30'; x.font = '700 110px ui-monospace, monospace'; x.textAlign = 'center'; x.fillText('08:42', 256, 118); }), toneMapped: false }));
  clock.position.set(-0.6, 2.45, z0 + 0.02); g.add(clock);
  g.traverse((o) => { if (o.isMesh && o.name !== 'floor') o.receiveShadow = true; });
  return mergeStatic(g);
}

/** Merge static meshes that share a material into one draw call each (transforms baked). */
export function mergeStatic(group) {
  group.updateMatrixWorld(true);
  const buckets = new Map(), keep = [];
  group.traverse((o) => {
    if (!o.isMesh) return;
    if (Array.isArray(o.material) || o.name === 'floor') { keep.push(o); return; }
    const k = o.material.uuid; if (!buckets.has(k)) buckets.set(k, []); buckets.get(k).push(o);
  });
  const out = new THREE.Group(); out.name = group.name;
  for (const o of keep) { o.updateMatrixWorld(true); const c = o.clone(); c.matrixAutoUpdate = false; c.matrix.copy(o.matrixWorld); out.add(c); }
  for (const list of buckets.values()) {
    const geos = list.map((o) => { const g2 = o.geometry.index ? o.geometry.toNonIndexed() : o.geometry.clone(); for (const n of Object.keys(g2.attributes)) if (!['position', 'normal', 'uv'].includes(n)) g2.deleteAttribute(n); return g2.applyMatrix4(o.matrixWorld); });
    const merged = mergeGeometries(geos, false);
    if (!merged) { list.forEach((o) => out.add(o.clone())); continue; }
    const m = new THREE.Mesh(merged, list[0].material); m.receiveShadow = true; out.add(m);
  }
  return out;
}

// ------------------------------------------------------------------ lighting
export async function setupLighting(renderer, scene, parent) {
  renderer.toneMapping = THREE.AgXToneMapping; renderer.toneMappingExposure = 1.15;
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const pmrem = new THREE.PMREMGenerator(renderer);
  const hdr = await new HDRLoader().loadAsync('./assets/env/surgery_1k.hdr');
  hdr.mapping = THREE.EquirectangularReflectionMapping;
  scene.environment = pmrem.fromEquirectangular(hdr).texture; scene.environmentIntensity = 0.55;
  scene.background = new THREE.Color(0xdfe5e8);
  hdr.dispose(); pmrem.dispose();
  parent.add(new THREE.HemisphereLight(0xf4f8ff, 0x9aa6a3, 0.9));
  // key light from the laminar-flow canopy: the only shadow caster
  const key = new THREE.DirectionalLight(0xf2f6ff, 1.6);
  key.position.set(TABLE_POS.x + 0.6, ROOM.h + 0.2, TABLE_POS.z + 0.8); key.target.position.copy(TABLE_POS);
  key.castShadow = true; key.shadow.mapSize.set(1024, 1024); key.shadow.bias = -0.0006; key.shadow.normalBias = 0.04; key.shadow.radius = 4;
  Object.assign(key.shadow.camera, { left: -3.2, right: 3.2, top: 3.2, bottom: -3.2, near: 0.5, far: 6 });
  parent.add(key, key.target);
  // fill from the ceiling LED panels
  const fill = new THREE.DirectionalLight(0xffffff, 0.5); fill.position.set(-2, ROOM.h, 1.5); parent.add(fill);
  return { key };
}

// ------------------------------------------------------------------ Sketchfab props
let loader;
export function gltfLoader(renderer) {
  if (loader) return loader;
  loader = new GLTFLoader();
  loader.setMeshoptDecoder(MeshoptDecoder);
  loader.setKTX2Loader(new KTX2Loader().setTranscoderPath('./vendor/basis/').detectSupport(renderer));
  return loader;
}
/**
 * Load a prop, scale it so its bounding-box `axis` equals `size` metres, put its base on the
 * floor at `pos`, rotate by `rotY`. Returns the wrapper group.
 */
export async function loadProp(renderer, name, { size, axis = 'y', pos = [0, 0, 0], rotY = 0, shadow = [0.8, 0.8], cast = true, onLoad } = {}) {
  const { scene } = await gltfLoader(renderer).loadAsync(`./assets/or/${name}.glb`);
  const b = new THREE.Box3().setFromObject(scene), s = new THREE.Vector3(); b.getSize(s);
  scene.scale.setScalar(size / s[axis]);
  const b2 = new THREE.Box3().setFromObject(scene), c = b2.getCenter(new THREE.Vector3());
  scene.position.set(-c.x, -b2.min.y, -c.z);
  const wrap = new THREE.Group(); wrap.name = `prop:${name}`; wrap.add(scene);
  wrap.position.set(...pos); wrap.rotation.y = rotY;
  scene.traverse((o) => {
    if (!o.isMesh) return;
    o.castShadow = cast; o.receiveShadow = true;
    if (!o.geometry.attributes.normal) { o.geometry.computeVertexNormals(); for (const m of [o.material].flat()) { m.flatShading = false; m.needsUpdate = true; } }
    const ms = Array.isArray(o.material) ? o.material : [o.material];
    for (const m of ms) {
      if (m.map) m.map.anisotropy = 4;
      if ('envMapIntensity' in m) m.envMapIntensity = 1;
      // transmission (glass) forces an extra full-scene render pass: use cheap transparency on Quest
      if (m.transmission > 0) { m.transmission = 0; m.transparent = true; m.opacity = 0.3; m.depthWrite = false; m.roughness = Math.min(m.roughness, 0.1); }
    }
  });
  if (shadow) wrap.add(Object.assign(contactShadow(shadow[0], shadow[1]), {}));
  onLoad?.(wrap, scene);
  return wrap;
}

export function sceneStats(root) {
  let tris = 0, draws = 0;
  root.traverse((o) => {
    if (!o.isMesh || !o.visible) return;
    const g = o.geometry, n = g.index ? g.index.count : g.attributes.position.count;
    draws += Array.isArray(o.material) ? g.groups.length || o.material.length : 1;
    tris += n / 3;
  });
  return { tris: Math.round(tris), draws };
}
