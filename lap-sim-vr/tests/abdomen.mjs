// Virtual-abdomen end-to-end check (headless Chrome + IWER Quest 3 emulation):
// runs the scripted procedure deterministically and screenshots the in-headset
// view of the OR monitor, plus full-frame scope views.
//   npm run build && npm start &   then   node tests/abdomen.mjs   -> artifacts/abd-*.png
import fs from 'node:fs';
import { chromium } from 'playwright';

const BASE = process.env.BASE_URL || 'http://localhost:8787';
const OUT = process.env.OUT || 'artifacts';
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.CHROME || (fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined),
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--autoplay-policy=no-user-gesture-required'],
});
const post = (ev) => fetch(`${BASE}/api/events`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(ev) });
const events = [];

async function open(query, viewport) {
  const page = await browser.newPage({ viewport });
  page.on('pageerror', (e) => console.log('pageerror', e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !m.text().includes('Failed to load resource')) console.log('console', m.text()); });
  await page.goto(`${BASE}/?clean=1&controllers=0&manual=1&${query}`);
  await page.waitForFunction(() => window.lapsim?.abd?.ready && window.lapsim.bus.connected, null, { timeout: 120000 });
  return page;
}
async function advanceTo(page, T) {
  const now = await page.evaluate(() => window.lapsim.autopilot?.t ?? 0);
  const n = Math.max(0, Math.round((T - now) * 30));
  for (let k = 0; k < n; k += 30) await page.evaluate((m) => window.lapsim.stepSim(m), Math.min(30, n - k));
}
async function shot(page, name) {
  await page.waitForTimeout(700);
  await page.evaluate(() => window.lapsim.snap());
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log('saved', `${OUT}/${name}.png`);
}

// ---- 1) in-headset view: OR monitor showing the virtual abdomen
await post({ type: 'reset' });
let page = await open('emulate=1&autopilot=1', { width: 1600, height: 900 });
await page.click('#vr-button');
await page.waitForFunction(() => window.lapsim.renderer.xr.isPresenting);
const look = (pos, at) => page.evaluate(([p, t]) => {
  const { THREE, device } = window.lapsim;
  const q = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().lookAt(new THREE.Vector3(...p), new THREE.Vector3(...t), new THREE.Vector3(0, 1, 0)));
  device.position.set(...p); device.quaternion.set(q.x, q.y, q.z, q.w);
}, [pos, at]);
await look([0.15, 1.7, 0.45], [0.45, 1.3, -1.5]);
await advanceTo(page, 6.5);
await shot(page, 'abd-01-or-with-virtual-abdomen');
await look([0.05, 1.65, 0.0], [0.3, 1.64, -1.65]);
await advanceTo(page, 12.6);
await shot(page, 'abd-02-vr-monitor-hook-smoke');
await advanceTo(page, 18.9);
await shot(page, 'abd-03-vr-monitor-clips');
await advanceTo(page, 24.0);
await shot(page, 'abd-04-vr-monitor-artery-bleed');
await look([0.1, 1.68, 0.4], [0.75, 1.5, -1.5]);
await shot(page, 'abd-05-or-complication');
console.log('stats', await page.evaluate(() => JSON.stringify(window.lapsim.abd.stats)));
await page.close();

// ---- 2) full-frame scope view (what the monitor shows), incl. the bile-leak variant
await post({ type: 'reset' });
page = await open('view=scope&autopilot=1', { width: 1280, height: 720 });
for (const [T, name] of [[5.0, 'abd-10-scope-calot'], [9.2, 'abd-11-scope-dissection'], [18.9, 'abd-12-scope-clipped'], [21.7, 'abd-13-scope-duct-divided'], [24.0, 'abd-14-scope-artery-bleed']]) {
  await advanceTo(page, T); await shot(page, name);
}
await page.close();
await post({ type: 'reset' });
page = await open('view=scope&autopilot=bile', { width: 1280, height: 720 });
await advanceTo(page, 22.5); await shot(page, 'abd-15-scope-bile-leak');
await page.close();
await post({ type: 'reset' });
await browser.close();
