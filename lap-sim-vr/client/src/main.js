import * as THREE from 'three';
import { VRButton } from 'three/addons/webxr/VRButton.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { XRControllerModelFactory } from 'three/addons/webxr/XRControllerModelFactory.js';
import { maybeEmulate } from './emulate.js';
import { buildPatient } from './or.js';
import { OutsideInstruments, abdomenToWorld } from './outside.js';
import { FrameStream } from './framestream.js';
import { loadPatient, abdomenOriginY } from './patient.js';
import { buildRoom, setupLighting, loadProp, sceneStats, contactShadow, TABLE_POS, ROOM } from './room.js';
import { LapMonitor } from './monitor.js';
import { VitalsMonitor } from './vitals.js';
import { ORAudio, Captions } from './audio.js';
import { VideoSource } from './video.js';
import { connectEvents } from './events.js';
import { Abdomen } from './abdomen/index.js';
import { Autopilot } from './abdomen/autopilot.js';
import { KeyboardTools, controlsPanel } from './abdomen/keyboard.js';
import { TOOL_TYPES, poseToShaft, PORTS } from './abdomen/tools.js';

const params = new URLSearchParams(location.search);
if (params.has('clean') || params.get('view') === 'scope') document.body.classList.add('clean'); // scope view: image only (+ controls card)
const device = await maybeEmulate(params);

// ------------------------------------------------------------------ renderer
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: params.has('emulate') || params.has('manual') });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.xr.enabled = true;
renderer.xr.setReferenceSpaceType('local-floor');
renderer.xr.setFoveation(0.5);
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x101418);
const world = new THREE.Group(); // everything; moved by "recenter"
scene.add(world);

const camera = new THREE.PerspectiveCamera(70, innerWidth / innerHeight, 0.05, 60);
camera.position.set(0, 1.65, 0.35);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0.3, 1.35, -1.4);
controls.enableDamping = true;
controls.update();


// ------------------------------------------------------------------ the OR
// Room shell + lighting (room.js) and real CC-BY models from Sketchfab (assets-src/or-models.json).
world.add(buildRoom());
const lighting = setupLighting(renderer, scene, world).catch((e) => console.error('lighting failed', e));
const TABLE_TOP = 0.74; // top of the OR table mattress (m)
const patient = buildPatient(TABLE_TOP);
patient.position.copy(TABLE_POS);
world.add(patient);
for (const p of Object.values(patient.userData.ports)) p.visible = false; // trocars now come from outside.js
// instruments + trocars in the room, in the abdomen frame mapped onto the belly (see outside.js)
// origin height from the baked body's skin at the ports (patient-landmarks.json), so trocars sit on the skin
const outside = new OutsideInstruments(world, abdomenToWorld(new THREE.Vector3(TABLE_POS.x + 0.12, abdomenOriginY(PORTS), TABLE_POS.z)));
// realistic patient (scanned body + baked cloth drapes); the procedural one stays only as a fallback
patient.visible = false;
const patientLoaded = loadPatient(renderer, world).catch((e) => { console.warn('patient.glb failed, using procedural fallback', e); patient.visible = true; });
const props = [
  // [model, options]
  ['bed', { size: 2.05, axis: 'x', pos: [TABLE_POS.x, 0, TABLE_POS.z], shadow: [2.4, 1.1] }],
  ['orlamps', { size: 1.24, pos: [TABLE_POS.x + 0.1, 3.0 - 1.24, TABLE_POS.z - 0.15], rotY: 0.575, shadow: null, cast: false }],
  ['pendant', { size: 1.8, pos: [1.75, 3.0 - 1.8, -1.75], rotY: Math.PI, shadow: null, cast: false }],
  ['anesthesia', { size: 1.45, pos: [1.95, 0, -0.75], rotY: -Math.PI / 2, shadow: [0.9, 1.0] }],
  ['endocart', { size: 1.45, pos: [0.35, 0, -1.95], shadow: [0.8, 0.8] }],
  ['roofmon', { size: 1.0, pos: [-1.1, 3.0 - 1.0, -2.2], rotY: 0.35, shadow: null, cast: false }],
  ['ivstand', { size: 1.95, pos: [1.25, 0, -0.05], shadow: [0.6, 0.6], cast: false }],
  ['suction', { size: 0.4, pos: [ROOM.x1 - 0.2, 0.95, -0.9], rotY: -Math.PI / 2, shadow: null }],
  ['trolley', { size: 0.95, pos: [-1.35, 0, 0.35], rotY: Math.PI / 2, shadow: [1.2, 0.8] }],
  ['cabinet', { size: 1.95, pos: [ROOM.x0 + 0.17, 0, -2.75], rotY: Math.PI / 2, shadow: [0.5, 1.6], cast: false }],
  ['surgeon', { size: 1.8, pos: [-1.3, 0, -1.3], rotY: 0.9, shadow: [0.8, 0.8] }],
  ['nurse', { size: 1.66, pos: [-2.05, 0, -0.1], rotY: 1.9, shadow: [0.7, 0.7] }],
];
const propsLoaded = Promise.allSettled(props.map(([name, o]) => loadProp(renderer, name, o).then((g) => { world.add(g); return g; })))
  .then((r) => { r.forEach((x, i) => { if (x.status === 'rejected') console.warn('prop failed', props[i][0], x.reason); }); });
// the surgical lights light the field: two spots from the lamp heads onto the abdomen
for (const [dx, dz] of [[-1.1, -0.15], [1.3, -0.15]]) {
  const sp = new THREE.SpotLight(0xfff8ee, 14, 3.5, 0.3, 0.55, 1.4);
  sp.position.set(TABLE_POS.x + dx, 1.85, TABLE_POS.z + dz); sp.target.position.set(TABLE_POS.x + 0.12, TABLE_TOP + 0.2, TABLE_POS.z + 0.02);
  world.add(sp, sp.target);
}

// main laparoscopy monitor on a boom arm above the tower, facing the surgeon
// (a 32" surgical display enlarged for headset readability, on the endoscopy tower)
const monitor = new LapMonitor({ width: Number(params.get('monitorWidth') || 1.2) });
monitor.group.position.set(0.35, 1.55, -1.82);
monitor.group.lookAt(0, 1.55, 0);
world.add(monitor.group);

// In-VR controller cheat-sheet on a small stand at the surgeon's left
{
  const c = document.createElement('canvas'); c.width = 768; c.height = 512;
  const x = c.getContext('2d');
  x.fillStyle = 'rgba(12,18,26,0.92)'; x.beginPath(); x.roundRect(0, 0, 768, 512, 28); x.fill();
  x.fillStyle = '#9fd0ff'; x.font = '700 38px system-ui'; x.fillText('Controllers = instruments', 32, 62);
  x.font = '500 27px system-ui'; x.fillStyle = '#dfe7ef';
  [['Left hand', 'grasper via right-subcostal port'], ['Right hand', 'instrument via epigastric port'], ['Move hand', 'tool pivots through the trocar'],
    ['Trigger', 'close jaws: grasp / clip / cut'], ['A / X', 'energy (hook cautery)'], ['B / Y', 'next instrument'], ['Grip', 'recenter the room'],
    ['', '(off when the box tracker is streaming)']].forEach(([k, v], i) => {
    x.fillStyle = '#ffd27a'; x.fillText(k, 32, 120 + i * 48); x.fillStyle = '#dfe7ef'; x.fillText(v, 220, 120 + i * 48);
  });
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  const panel = new THREE.Mesh(new THREE.PlaneGeometry(0.48, 0.32), new THREE.MeshBasicMaterial({ map: tex, transparent: true, toneMapped: false }));
  panel.position.set(-0.85, 1.05, -0.15); panel.lookAt(0, 1.7, 0.3); panel.name = 'vr-controls-help';
  world.add(panel);
}
const captions = new Captions({ width: 1.4 });
captions.mesh.position.set(0, -monitor.height / 2 - 0.15, 0.04);
monitor.group.add(captions.mesh);

// anaesthesia vitals monitor on a pole at the head end
const vitals = new VitalsMonitor({ width: 0.66 });
vitals.group.position.set(1.5, 1.62, -1.45);
vitals.group.lookAt(0, 1.55, 0);
world.add(vitals.group);
const vpole = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 1.25, 12), new THREE.MeshStandardMaterial({ color: 0x9aa3ad, metalness: 0.8, roughness: 0.3 }));
vpole.visible = false; // the vitals monitor now sits on the ceiling pendant

// ------------------------------------------------------------------ audio
const audio = new ORAudio();
vitals.onBeat((v) => audio.beat(v));
addEventListener('pointerdown', () => { audio.unlock(); video.resume(); });

// ------------------------------------------------------------------ status HUD (2D page only)
const $ = (id) => document.getElementById(id);
function status(dotId, textId, cls, text) { $(dotId).className = `dot ${cls}`; $(textId).textContent = text; }

// ------------------------------------------------------------------ video
const video = new VideoSource({
  onVideo: (el, label) => { if (sourceMode !== 'sim') monitor.setVideo(el, label); },
  onStatus: (mode, detail) => status('src-dot', 'src-text', { live: 'ok', demo: 'warn', connecting: 'warn' }[mode] || 'bad', `video: ${detail}`),
});
const config = await fetch('./api/config').then((r) => r.json()).catch(() => ({ whep: '/mediamtx/cam/whep', defaultSource: 'auto' }));
const whepOverride = params.get('whep');
let sourceMode = params.get('source') || config.defaultSource || 'sim';

// ------------------------------------------------------------------ virtual abdomen (source "sim")
let abd = null, abdLoading = null, autopilot = null, keyTools = null, helpPanel = null;
function ensureAbdomen() {
  abdLoading ??= (async () => {
    const a = new Abdomen({
      ...(([w, h]) => ({ width: w, height: h }))((params.get('simRes') || '1280x720').split('x').map(Number)), fat: params.get('fat') !== '0', scopeAngle: Number(params.get('scopeAngle') || 0) * Math.PI / 180,
      onEvent: (ev) => {
        if (ev.type === 'sim') helpPanel?.note(`${ev.action}: ${ev.target}${ev.total ? ` (${ev.total} clips)` : ''}`);
        if (!bus.send(ev)) apply({ ...ev, ts: Date.now(), from: 'sim' });
      },
    });
    await a.init();
    abd = a;
    if (params.has('autopilot')) autopilot = new Autopilot(abd, { variant: params.get('autopilot') });
    const scope = params.get('view') === 'scope';
    if (!params.has('emulate') || params.has('keys')) {
      keyTools = new KeyboardTools(abd, renderer.domElement, {
        requireShift: !scope, // in the OR view a plain drag orbits the room
        onToggleAutopilot: () => { autopilot = autopilot ? null : new Autopilot(abd); helpPanel?.note(autopilot ? 'autopilot running (P stops)' : 'autopilot stopped'); },
      });
      if (!params.has('clean') || params.has('help')) {
        helpPanel = controlsPanel({ keyTools, title: scope ? 'Instrument controls (scope view)' : 'Instrument controls (desktop)', showInput: params.has('showInput'),
          extra: scope ? [] : [['Shift + drag / Shift + wheel', 'move the tool / depth (plain drag orbits the room)'], ['?view=scope', 'full-screen laparoscope view']] });
      }
    }
    return a;
  })();
  return abdLoading;
}
const frames = new FrameStream({
  onStatus: (st, detail) => { if (sourceMode === 'frames') status('src-dot', 'src-text', st === 'live' ? 'ok' : 'bad', `video: ${detail}`); },
});
async function setSource(mode, whep, opts = {}) {
  sourceMode = mode;
  if (mode !== 'frames') frames.stop();
  if (mode === 'frames') {
    video.stopLive(); video.demo.pause();
    frames.configure({ url: opts.url ?? params.get('frameUrl') ?? frames.cfg.url, ws: opts.ws ?? params.get('frameWs') ?? frames.cfg.ws, pollMs: opts.pollMs ?? Number(params.get('framePoll') ?? frames.cfg.pollMs) });
    monitor.setTexture(frames.texture, 'LIVE · AI', { linear: false });
    status('src-dot', 'src-text', 'warn', 'video: waiting for AI frames…');
  } else if (mode === 'sim') {
    video.stopLive(); video.demo.pause();
    status('src-dot', 'src-text', 'warn', 'video: loading virtual abdomen…');
    const a = await ensureAbdomen();
    if (sourceMode !== 'sim') return;
    monitor.setTexture(a.texture, 'SIM · virtual abdomen');
    status('src-dot', 'src-text', 'ok', 'video: virtual abdomen (XPBD sim)');
  } else video.configure({ mode, whep: whepOverride || whep || config.whep });
}
setSource(sourceMode, config.whep, {});

// ------------------------------------------------------------------ events
function apply(ev) {
  switch (ev.type) {
    case 'state': {
      const s = ev.state;
      monitor.setBleed(s.bleed);
      if (s.smoke.intensity > 0 && (!s.smoke.until || s.smoke.until > Date.now())) monitor.setSmoke({ intensity: s.smoke.intensity, durationMs: s.smoke.until ? s.smoke.until - Date.now() : 0 });
      monitor.setWarning(s.warning && (!s.warning.until || s.warning.until > Date.now()) ? { ...s.warning, durationMs: s.warning.until ? s.warning.until - Date.now() : 0 } : null);
      vitals.set({ ...s.vitals, transitionMs: 0 }); vitals.snap();
      if (!params.get('source') && s.source?.mode && (s.source.mode !== sourceMode || s.source.mode === 'frames')) setSource(s.source.mode, s.source.whep, s.source);
      monitor.setBile(s.bile || { intensity: 0 });
      break;
    }
    case 'bleed': monitor.setBleed(ev); break;
    case 'smoke': monitor.setSmoke(ev); break;
    case 'warning': monitor.setWarning(ev); break;
    case 'clear': {
      const t = ev.target || 'all';
      if (t === 'all' || t === 'bleed') monitor.setBleed({ intensity: 0 });
      if (t === 'all' || t === 'bile') monitor.setBile({ intensity: 0 });
      if (t === 'all' || t === 'smoke') monitor.setSmoke({ intensity: 0, durationMs: 0 });
      if (t === 'all' || t === 'warning') monitor.setWarning(null);
      if (t === 'all' || t === 'captions') captions.clear();
      break;
    }
    case 'vitals': vitals.set(ev); break;
    case 'say': captions.push(ev.speaker, ev.text); audio.say(ev); break;
    case 'source': setSource(ev.mode, ev.whep, ev); break;
    case 'frame': if (sourceMode === 'frames') frames.refresh('bus', ev.url); break;
    case 'bile': monitor.setBile(ev); break;
    case 'tools':
      if (ev.from === 'hardware' || ev.from === 'tracker') { trackerSeen = performance.now(); trackerCount++; }
      if (abd && ev.from !== 'display') abd.setPoses(ev.tools);
      break;
    case 'tracker': showTracker(ev); break;
    case 'sim':
      if (ev.action === 'reset' && abd) { abdLoading = null; abd = null; autopilot = null; setSource(sourceMode); }
      if (ev.action === 'autopilot' && abd) { abd.lastExternal = 0; autopilot = new Autopilot(abd, { variant: ev.target }); }
      if (ev.action === 'stop') autopilot = null;
      break;
    case 'reset':
      monitor.setBleed({ intensity: 0 }); monitor.setBile({ intensity: 0 }); monitor.setSmoke({ intensity: 0, durationMs: 0 }); monitor.setWarning(null); captions.clear();
      vitals.set({ hr: 78, bp: '122/76', spo2: 99, rr: 12, etco2: 36, temp: 36.8, transitionMs: 2000 });
      break;
  }
}
const bus = connectEvents({
  role: 'display', name: device ? 'emulated-quest' : navigator.userAgent.includes('OculusBrowser') ? 'quest' : 'browser',
  onEvent: apply,
  onStatus: (ok) => status('ws-dot', 'ws-text', ok ? 'ok' : 'bad', ok ? 'director link: connected' : 'director link: reconnecting…'),
});

// ------------------------------------------------------------------ XR: controllers + recenter
const controllerModels = new XRControllerModelFactory();
for (let i = 0; i < 2; i++) {
  const grip = renderer.xr.getControllerGrip(i);
  // ?controllers=0 hides the models (the surgeon's hands are on real instruments anyway)
  if (params.get('controllers') !== '0') grip.add(controllerModels.createControllerModel(grip));
  scene.add(grip);
  const ctrl = renderer.xr.getController(i);
  ctrl.addEventListener('squeezestart', recenter);
  scene.add(ctrl);
}
/** Move the OR so the surgeon spot is where the user's head is now, facing where they look. */
function recenter() {
  const xrCam = renderer.xr.getCamera();
  const pos = new THREE.Vector3(), q = new THREE.Quaternion();
  xrCam.getWorldPosition(pos); xrCam.getWorldQuaternion(q);
  const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(q);
  world.rotation.y = Math.atan2(-fwd.x, -fwd.z);
  world.position.set(pos.x, 0, pos.z);
}
renderer.xr.addEventListener('sessionstart', () => { audio.unlock(); video.resume(); });

// Without the physical tracker, the Quest controllers act as instrument handles: each one
// pivots through a virtual trocar on the draped patient (left hand -> right-subcostal port,
// right hand -> epigastric port). Trigger closes the jaws, A/X = energy, B/Y = next instrument.
let trackerSeen = -1e9, trackerCount = 0, trackerHz = 0;
setInterval(() => { trackerHz = trackerCount; trackerCount = 0; if (performance.now() - trackerSeen > 1000) showTracker({ live: false, hz: 0, tools: [] }); }, 1000); // live status + rate come from the server's 1 Hz 'tracker' event
function showTracker(t) {
  const el = document.getElementById('trk-text'), dot = document.getElementById('trk-dot');
  const txt = t.live ? `TRACKER LIVE · ${t.hz} Hz${t.tools?.length ? ' · ' + t.tools.join(', ') : ''}` : 'NO TRACKER (controllers / keyboard drive the tools)';
  if (el) { el.textContent = txt; dot.className = `dot ${t.live ? 'ok' : 'bad'}`; }
  monitor.setTracker?.(t.live ? `TRACKER ${t.hz} Hz` : null);
}
const handPorts = { left: 'rsc', right: 'epigastric' };
const btnPrev = { left: false, right: false };
function controllerTools() {
  const session = renderer.xr.getSession();
  if (!session || !abd || autopilot || performance.now() - trackerSeen < 1000 || performance.now() - abd.lastExternal < 1000 || params.get('controllerTools') === '0') return;
  const poses = [];
  const H = new THREE.Vector3(), T = new THREE.Vector3();
  for (const src of session.inputSources) {
    const hand = src.handedness; if (!handPorts[hand] || !src.gamepad) continue;
    renderer.xr.getControllerGrip([...session.inputSources].indexOf(src)).getWorldPosition(H);
    world.worldToLocal(H); // controller handle in OR coordinates -> pose through the trocar (exact inverse of outside.js)
    const fp = outside.poseFromHandle(handPorts[hand], H);
    const b = src.gamepad.buttons, id = hand === 'left' ? 'left' : 'right';
    const cur = abd.poses[id];
    const next = b[5]?.pressed; if (next && !btnPrev[hand]) cur.tool = TOOL_TYPES[(TOOL_TYPES.indexOf(cur.tool) + 1) % TOOL_TYPES.length]; btnPrev[hand] = next;
    poses.push({ id, port: handPorts[hand], tool: cur.tool, ...fp, roll: 0, jaw: 1 - (b[0]?.value ?? 0), energy: !!b[4]?.pressed });
  }
  if (poses.length) abd.setPoses(poses, false);
}

const manualSim = params.has('manual'); // tests advance the sim deterministically via lapsim.stepSim()
function stepSim(dt) {
  if (autopilot) abd.setPoses(autopilot.tick(dt), false);
  else if (renderer.xr.isPresenting) controllerTools();
  keyTools?.update(dt);
  abd.hr = vitals.cur.hr;
  abd.update(dt);
}

// ?view=scope: show only the laparoscope monitor image full-screen (desktop testing)
const scopeView = params.get('view') === 'scope' ? (() => {
  const sc = new THREE.Scene(), cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  sc.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), monitor.screen.material));
  return { scene: sc, camera: cam };
})() : null;

// ------------------------------------------------------------------ loop
const timer = new THREE.Timer(); timer.connect(document);
let renderRequested = false;
renderer.setAnimationLoop((time) => {
  timer.update(time);
  if (manualSim && !renderRequested && !renderer.xr.isPresenting) return; // tests: draw only after a step
  renderRequested = false;
  const dt = Math.min(timer.getDelta(), 0.1);
  const t = timer.getElapsed();
  monitor.setHeartRate(vitals.cur.hr);
  monitor.update(dt, t);
  vitals.update(dt, t);
  captions.update();
  frames.update(dt);
  if (helpPanel && Math.floor(t * 10) !== Math.floor((t - dt) * 10)) helpPanel.update();
  if (abd) outside.update(abd.poses);
  if (abd && sourceMode === 'sim') {
    if (!manualSim) stepSim(dt);
    abd.render(renderer);
  }
  if (scopeView) { renderer.render(scopeView.scene, scopeView.camera); return; }
  if (!renderer.xr.isPresenting) controls.update();
  renderer.render(scene, camera);
});

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

const vrButton = VRButton.createButton(renderer, { optionalFeatures: ['local-floor', 'bounded-floor', 'hand-tracking'] });
vrButton.id = 'vr-button';
if (!scopeView) document.body.appendChild(vrButton);

// Hooks for tests / debugging in the console
window.lapsim = {
  THREE, renderer, scene, camera, controls, world, monitor, vitals, captions, video, bus, device, apply, recenter, propsLoaded: Promise.all([propsLoaded, patientLoaded]),
  stats() { return { scene: sceneStats(world), drawCalls: renderer.info.render.calls, frameTris: renderer.info.render.triangles }; },
  frames, outside, get trackerLive() { return performance.now() - trackerSeen < 1000; },
  get abd() { return abd; }, get autopilot() { return autopilot; }, get keyTools() { return keyTools; }, setSource, poseToShaft,
  stepSim(n = 1, dt = 1 / 30) { for (let i = 0; i < n; i++) stepSim(dt); renderRequested = true; },
  snap() { monitor.snap(); vitals.snap(); },
};
