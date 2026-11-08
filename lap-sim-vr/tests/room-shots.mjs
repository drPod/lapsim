// Fixed-pose in-headset screenshots of the OR (IWER Quest 3) + the scope view.
//   node tests/room-shots.mjs <prefix>   -> artifacts/<prefix>-{wide,eye,table,scope}.png
import fs from 'node:fs';
import { chromium } from 'playwright';
const BASE = process.env.BASE_URL || 'http://localhost:8787';
const prefix = process.argv[2] || 'room';
const OUT = process.env.OUT || 'artifacts'; fs.mkdirSync(OUT, { recursive: true });
await fetch(`${BASE}/api/events`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"type":"reset"}' });
const browser = await chromium.launch({ executablePath: fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
page.on('pageerror', (e) => console.log('pageerror', e.message));
await page.goto(`${BASE}/?emulate=1&clean=1&controllers=0&manual=1&autopilot=1`);
await page.waitForFunction(() => window.lapsim?.abd?.ready, null, { timeout: 180000 });
await page.waitForTimeout(4000); // props
await page.click('#vr-button');
await page.waitForFunction(() => window.lapsim.renderer.xr.isPresenting);
await page.evaluate(() => window.lapsim.stepSim(150)); // autopilot: tools in view
const shot = async (name, pos, at) => {
  await page.evaluate(([p, t]) => {
    const { THREE, device } = window.lapsim;
    const q = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().lookAt(new THREE.Vector3(...p), new THREE.Vector3(...t), new THREE.Vector3(0, 1, 0)));
    device.position.set(...p); device.quaternion.set(q.x, q.y, q.z, q.w);
  }, [pos, at]);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/${prefix}-${name}.png` }); console.log('saved', `${OUT}/${prefix}-${name}.png`);
};
await shot('wide', [-2.5, 1.85, 1.5], [0.4, 1.0, -1.0]);
await shot('eye', [0.0, 1.65, 0.05], [0.3, 1.45, -1.6]);
await shot('table', [0.05, 1.6, -0.05], [0.25, 0.95, -0.85]);
if (prefix !== 'before') console.log('stats', await page.evaluate(() => JSON.stringify(window.lapsim.stats?.())));
await page.close();
const p2 = await browser.newPage({ viewport: { width: 1280, height: 720 } });
await p2.goto(`${BASE}/?view=scope&clean=1&manual=1&autopilot=1`);
await p2.waitForFunction(() => window.lapsim?.abd?.ready, null, { timeout: 180000 });
await p2.evaluate(() => window.lapsim.stepSim(160));
await p2.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
await p2.screenshot({ path: `${OUT}/${prefix}-scope.png` }); console.log('saved', `${OUT}/${prefix}-scope.png`);
await browser.close();
