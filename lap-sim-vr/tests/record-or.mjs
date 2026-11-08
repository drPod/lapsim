// Camera fly-in through the OR (desktop renderer) with the autopilot running on the
// monitor, captured frame by frame: artifacts/or-frames/*.png
import fs from 'node:fs';
import { chromium } from 'playwright';
const BASE = process.env.BASE_URL || 'http://localhost:8787';
const dir = `${process.env.OUT || 'artifacts'}/or-frames`; fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
const browser = await chromium.launch({ executablePath: fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: 960, height: 540 } });
await page.goto(`${BASE}/?clean=1&manual=1&autopilot=1&simRes=960x540`);
await page.waitForFunction(() => window.lapsim?.abd?.ready, null, { timeout: 180000 });
await page.evaluate(() => window.lapsim.propsLoaded);
await page.evaluate(() => { document.getElementById('vr-button')?.remove(); window.lapsim.stepSim(120); });
const keys = [ // [pos, target]
  [[-2.7, 1.95, 1.7], [0.4, 1.0, -1.0]], [[-1.6, 1.85, 1.1], [0.35, 1.1, -1.1]], [[-0.4, 1.72, 0.45], [0.3, 1.25, -1.3]], [[0.0, 1.65, 0.05], [0.33, 1.5, -1.7]],
];
const N = Number(process.argv[2] || 72);
const lerp = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
for (let f = 0; f < N; f++) {
  const u = Math.min(1, f / (N * 0.75)), s = u * (keys.length - 1), k = Math.min(keys.length - 2, Math.floor(s)), t = s - k, e = t * t * (3 - 2 * t);
  const pos = lerp(keys[k][0], keys[k + 1][0], e), at = lerp(keys[k][1], keys[k + 1][1], e);
  await page.evaluate(([p, a]) => { const L = window.lapsim; L.camera.position.set(...p); L.controls.target.set(...a); L.controls.update(); L.stepSim(4); }, [pos, at]);
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  await page.screenshot({ path: `${dir}/${String(f).padStart(4, '0')}.png` });
}
await browser.close(); console.log('done', N);
