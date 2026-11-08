// Box-tracker protocol test: a scripted WebSocket client says hello as role "hardware" and
// streams {type:"tools"} poses at 30 Hz for three sticks (epigastric, rsc, raa), sweeping
// each left/right (yaw) then in/out (depth). Two displays are captured frame by frame:
// the OR (outside instruments through the trocars) and the scope view.
//   BASE_URL=https://8787-34255b7eef89.replicas.dev node tests/tracker.mjs
import fs from 'node:fs';
import WebSocket from 'ws';
import { chromium } from 'playwright';

const BASE = process.env.BASE_URL || 'http://localhost:8787';
const OUT = process.env.OUT || 'artifacts';
const dirs = { or: `${OUT}/trk-or`, scope: `${OUT}/trk-scope` };
for (const d of Object.values(dirs)) { fs.rmSync(d, { recursive: true, force: true }); fs.mkdirSync(d, { recursive: true }); }

// ---- the "tracker"
const ws = new WebSocket(BASE.replace(/^http/, 'ws') + '/ws');
await new Promise((r, j) => { ws.once('open', r); ws.once('error', j); });
ws.send(JSON.stringify({ type: 'hello', role: 'hardware', name: 'test-box-tracker' }));
let BASEPOSE = { epigastric: { tool: 'maryland', yaw: 0.0, pitch: 0.05, depth: 0.1 }, rsc: { tool: 'grasper', yaw: 0.0, pitch: -0.05, depth: 0.09 }, raa: { tool: 'grasper', yaw: 0.0, pitch: 0.0, depth: 0.08 } };
const N = Number(process.argv[2] || 48);
const poseAt = (f) => {
  const half = N / 2, u = (f % half) / half, s = Math.sin(u * Math.PI * 2);
  return Object.entries(BASEPOSE).map(([port, b]) => ({
    id: port, port, tool: b.tool, roll: 0, jaw: 1, energy: false, pitch: b.pitch,
    yaw: f < half ? b.yaw + 0.22 * s : b.yaw,              // phase 1: left/right
    depth: f < half ? b.depth : b.depth + 0.035 * s,        // phase 2: in/out
  }));
};
let current = null;
const timer = setInterval(() => current && ws.send(JSON.stringify({ type: 'tools', t: Date.now(), tools: current })), 1000 / 30);

// ---- the displays
const browser = await chromium.launch({ executablePath: fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const open = async (q, w, h) => {
  const p = await browser.newPage({ viewport: { width: w, height: h } });
  p.on('pageerror', (e) => console.log('pageerror', e.message));
  await p.addInitScript(() => localStorage.setItem('lapsim-help-hidden', '1'));
  await p.goto(`${BASE}/?manual=1&simRes=960x540&${q}`);
  await p.waitForFunction(() => window.lapsim?.abd?.ready, null, { timeout: 180000 });
  return p;
};
const or = await open('controllers=0', 960, 540);
await or.evaluate(() => window.lapsim.propsLoaded);
await or.evaluate(() => { document.getElementById('vr-button')?.remove(); const L = window.lapsim; L.camera.position.set(-0.3, 1.38, 0.0); L.controls.target.set(0.3, 0.95, -0.85); L.controls.update(); });
const scope = await open('view=scope', 960, 540);
// a calibrated tracker at rest: start from the in-view ready poses
BASEPOSE = Object.fromEntries((await scope.evaluate(() => Object.values(window.lapsim.abd.poses).filter((p) => p.port !== 'umbilical').map((p) => ({ ...p }))))
  .map((p) => [p.port, { tool: p.tool, yaw: p.yaw, pitch: p.pitch, depth: p.depth }]));
current = poseAt(0);
await or.waitForFunction(() => window.lapsim.trackerLive && /TRACKER LIVE/.test(document.getElementById('trk-text').textContent), null, { timeout: 15000 });
console.log('HUD:', await or.evaluate(() => document.getElementById('trk-text').textContent));

const tipInfo = (p) => p.evaluate(() => {
  const L = window.lapsim, a = L.abd, s = L.poseToShaft(a.poses.right), v = new L.THREE.Vector3(...s.tip);
  const world = v.clone().applyMatrix4(L.outside.M); const scr = v.clone().project(a.camera);
  return { worldX: world.x, worldZ: world.z, screenX: scr.x, screenY: scr.y };
});
const checks = [];
for (let f = 0; f < N; f++) {
  current = poseAt(f);
  const want = current.find((t) => t.port === 'epigastric');
  for (const p of [or, scope]) {
    await p.waitForFunction((w) => Math.abs(window.lapsim.abd.poses.right.yaw - w.yaw) < 1e-4 && Math.abs(window.lapsim.abd.poses.right.depth - w.depth) < 1e-4, want, { timeout: 15000 });
    await p.evaluate(() => window.lapsim.stepSim(2));
    await p.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  }
  await or.screenshot({ path: `${dirs.or}/${String(f).padStart(4, '0')}.png` });
  await scope.screenshot({ path: `${dirs.scope}/${String(f).padStart(4, '0')}.png` });
  checks.push({ f, yaw: want.yaw, depth: want.depth, ...(await tipInfo(or)) });
}
// tip motion must agree between the room and the monitor (same direction left/right)
const a = checks[Math.round(N / 8)], b = checks[Math.round(3 * N / 8)]; // yaw max vs yaw min
console.log('yaw', a.yaw.toFixed(2), '->', b.yaw.toFixed(2), '| tip world dX', (b.worldX - a.worldX).toFixed(3), '| tip screen dX', (b.screenX - a.screenX).toFixed(3),
  '| agree:', Math.sign(b.worldX - a.worldX) === Math.sign(b.screenX - a.screenX));
clearInterval(timer); ws.close();
await new Promise((r) => setTimeout(r, 2500));
console.log('after stop, HUD:', await or.evaluate(() => document.getElementById('trk-text').textContent));
await browser.close();
