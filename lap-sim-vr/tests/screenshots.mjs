// Headless end-to-end check: opens the app in Chrome with Meta's IWER emulating a
// Quest 3, enters an immersive-vr session, fires events through the real server
// REST API, and screenshots the in-headset view for each overlay.
//
//   npm run build && npm start &      # server on :8787
//   npm run test:screens              # -> artifacts/*.png
//
// Env: BASE_URL (default http://localhost:8787), CHROME (path to a Chrome binary), OUT (artifacts dir)
import fs from 'node:fs';
import { chromium } from 'playwright';

const BASE = process.env.BASE_URL || 'http://localhost:8787';
const OUT = process.env.OUT || 'artifacts';
const EXTRA = process.env.QUERY || '';
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({
  executablePath: process.env.CHROME || (fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined),
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--autoplay-policy=no-user-gesture-required', '--ignore-certificate-errors'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, ignoreHTTPSErrors: true });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });

async function post(ev) {
  const r = await fetch(`${BASE}/api/events`, { method: 'POST', headers: { 'content-type': 'application/json', 'x-sender': 'test' }, body: JSON.stringify(ev) });
  const j = await r.json();
  if (!j.ok) throw new Error(`event rejected: ${JSON.stringify(j)}`);
}

/** Point the emulated headset from `pos` toward `target` (world metres). */
async function look(pos, target) {
  await page.evaluate(([p, t]) => {
    const { THREE, device } = window.lapsim;
    const m = new THREE.Matrix4().lookAt(new THREE.Vector3(...p), new THREE.Vector3(...t), new THREE.Vector3(0, 1, 0));
    const q = new THREE.Quaternion().setFromRotationMatrix(m);
    device.position.set(...p);
    device.quaternion.set(q.x, q.y, q.z, q.w);
  }, [pos, target]);
  await page.waitForTimeout(400);
}
async function shot(name, settle = 600) {
  await page.waitForTimeout(settle);
  await page.evaluate(() => window.lapsim.snap());
  await page.waitForTimeout(150);
  await page.screenshot({ path: `${OUT}/${name}.png` });
  console.log('saved', `${OUT}/${name}.png`);
}

await page.goto(`${BASE}/?emulate=1&clean=1&controllers=0${EXTRA}`);
await page.waitForFunction(() => window.lapsim && window.lapsim.bus.connected, null, { timeout: 30000 });
await page.waitForFunction(() => window.lapsim.scene.getObjectByName('room'), null, { timeout: 30000 });
await page.waitForTimeout(2500); // props + video
await post({ type: 'reset' });

// Enter the emulated immersive session via the real VRButton.
await page.click('#vr-button');
await page.waitForFunction(() => window.lapsim.renderer.xr.isPresenting, null, { timeout: 10000 });

const EYE = [0, 1.65, 0.05];
const MONITOR = [0.3, 1.64, -1.65];

await look([0.15, 1.7, 0.45], [0.45, 1.25, -1.5]);
await shot('01-or-overview');

await look(EYE, [-0.6, 1.2, -1.2]);
await shot('02-or-left');

await look([0.95, 1.62, -0.45], [1.45, 1.6, -1.2]);
await post({ type: 'vitals', hr: 78, bp: '122/76', spo2: 99, transitionMs: 0 });
await shot('03-vitals-monitor');

await look(EYE, MONITOR);
await shot('04-monitor-feed');

await post({ type: 'bleed', intensity: 0.6, pulsatile: true });
await shot('05-overlay-bleed', 300);
await post({ type: 'bleed', intensity: 0 });
await page.evaluate(() => window.lapsim.snap());

await post({ type: 'smoke', intensity: 0.8, durationMs: 0 });
await shot('06-overlay-smoke', 300);
await post({ type: 'clear', target: 'smoke' });

await post({ type: 'warning', text: 'Bleeding — cystic artery', level: 'critical' });
await post({ type: 'say', speaker: 'Anesthesia', text: 'Pressure is dropping. Eighty-four over forty-eight.' });
await shot('07-overlay-warning-caption', 300);

// full complication: everything at once, wide view so the vitals alarm shows too
await post({ type: 'bleed', intensity: 0.9, pulsatile: true });
await post({ type: 'vitals', hr: 134, bp: '82/46', spo2: 88, etco2: 28, transitionMs: 0 });
await look([0.1, 1.68, 0.4], [0.75, 1.5, -1.5]);
await shot('08-complication-wide', 300);

await post({ type: 'reset' });
await page.evaluate(() => window.lapsim.snap());

const state = await page.evaluate(() => ({
  presenting: window.lapsim.renderer.xr.isPresenting,
  source: window.lapsim.monitor.sourceLabel,
  props: window.lapsim.world.children.filter((o) => o.name.endsWith('.glb')).map((o) => o.name),
}));
console.log(JSON.stringify(state));
if (errors.length) console.log('page errors:\n' + errors.join('\n'));
await browser.close();
