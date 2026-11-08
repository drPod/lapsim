// Desktop scope view of the virtual abdomen (no OR, no VR): /abdomen.html
// ?autopilot=1 runs the scripted procedure, ?clean=1 hides the help, ?fat=0 removes the fat sheet.
import * as THREE from 'three';
import { Abdomen } from './index.js';
import { KeyboardTools, controlsPanel } from './keyboard.js';
import { Autopilot } from './autopilot.js';
import { connectEvents } from '../events.js';

const params = new URLSearchParams(location.search);
if (params.has('clean')) document.body.classList.add('clean');
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = Number(params.get('exposure') || 1.0);
document.body.appendChild(renderer.domElement);

let bus = null;
const abd = new Abdomen({ width: innerWidth, height: innerHeight, fat: params.get('fat') !== '0', onEvent: (ev) => { console.log('[sim event]', JSON.stringify(ev)); bus?.send(ev); } });
await abd.init();
abd.paused = params.has('paused');
if (params.get('cam')) { const v = params.get('cam').split(',').map(Number); abd.debugCam = { pos: v.slice(0, 3), at: v.slice(3, 6) }; abd.light.intensity *= 6; }
if (params.get('bus') !== '0') bus = connectEvents({ role: 'display', name: 'scope-view', onEvent: (ev) => { if (ev.type === 'tools') abd.setPoses(ev.tools); } });
const auto0 = params.has('autopilot');
const keys = new KeyboardTools(abd, renderer.domElement, { onToggleAutopilot: () => { autoRef.a = autoRef.a ? null : new Autopilot(abd); } });
const autoRef = { a: null };
const panel = params.has('clean') && !params.has('help') ? null : controlsPanel({ keyTools: keys, title: 'Instrument controls', showInput: params.has('showInput') });
document.getElementById('help')?.remove();
if (auto0) autoRef.a = new Autopilot(abd, { variant: params.get('autopilot') });

const timer = new THREE.Timer(); timer.connect(document);
let manual = params.has('manual'); // tests step the sim explicitly
function frame(dt, draw = true) {
  if (autoRef.a) abd.setPoses(autoRef.a.tick(dt), false);
  panel?.update();
  keys.update(dt);
  abd.update(dt);
  if (draw) { if (!abd.scene.environment) abd.buildEnv(renderer); renderer.render(abd.scene, abd.camera); }
}
renderer.setAnimationLoop((time) => { timer.update(time); if (!manual) frame(timer.getDelta()); });
addEventListener('resize', () => { renderer.setSize(innerWidth, innerHeight); abd.camera.aspect = innerWidth / innerHeight; abd.camera.updateProjectionMatrix(); });
window.abd = abd; window.THREE = THREE;
window.lapabd = { abd, get auto() { return autoRef.a; }, keys, step(n = 1, dt = 1 / 30, draw = true) { manual = true; for (let i = 0; i < n; i++) frame(dt, draw && i === n - 1); }, setManual(v) { manual = v; } };
window.lapabdReady = true;
