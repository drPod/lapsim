// Frame-by-frame capture of the scripted procedure in the scope view (with monitor
// overlays), deterministic regardless of render speed: artifacts/abd-frames/*.png
//   node tests/record-abdomen.mjs [fps=15] [from=4] [to=25] [variant]
import fs from 'node:fs';
import { chromium } from 'playwright';
const BASE = process.env.BASE_URL || 'http://localhost:8787';
const [fps = 15, from = 4, to = 25, variant = '1'] = process.argv.slice(2);
const dir = `${process.env.OUT || 'artifacts'}/abd-frames`; fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
await fetch(`${BASE}/api/events`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"type":"reset"}' });
const browser = await chromium.launch({ executablePath: fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: 960, height: 540 } });
await page.goto(`${BASE}/?clean=1&manual=1&view=scope&simRes=960x540&autopilot=${variant}`);
await page.waitForFunction(() => window.lapsim?.abd?.ready && window.lapsim.bus.connected, null, { timeout: 120000 });
const per = Math.round(30 / fps);
await page.evaluate((n) => window.lapsim.stepSim(n), Math.round(from * 30));
const frames = Math.round((to - from) * fps);
for (let f = 0; f < frames; f++) {
  await page.evaluate((n) => window.lapsim.stepSim(n), per);
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))); // let it draw
  await page.screenshot({ path: `${dir}/${String(f).padStart(4, '0')}.png` });
  if (f % 30 === 0) console.log('frame', f, '/', frames);
}
await browser.close();
console.log('done', dir);
