import { chromium } from '/home/user/workspaces/lap-sim-vr/node_modules/playwright/index.mjs';
const BASE = process.env.BASE || 'https://localhost:5174';
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome', args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-certificate-errors'] });
const p = await b.newPage({ viewport: { width: 1280, height: 720 }, ignoreHTTPSErrors: true });
p.on('pageerror', (e) => console.log('ERR', e.message));
p.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') { const t = m.text(); if (!t.includes('Failed to load resource') && !t.includes('GL_')) console.log(m.type(), t.slice(0, 200)); } });
await p.goto(`${BASE}/?clean=1&manual=1&controllers=0`);
await p.waitForFunction(() => window.lapsim?.abd?.ready, null, { timeout: 180000 });
await p.evaluate(() => window.lapsim.propsLoaded);
await p.waitForTimeout(1500);
const views = { wide: [[-2.5, 1.85, 1.5], [0.4, 1.0, -1.0]], eye: [[0.0, 1.65, 0.05], [0.3, 1.45, -1.6]], table: [[0.05, 1.6, -0.05], [0.25, 0.95, -0.85]], head: [[2.9, 1.7, 1.6], [0.6, 1.0, -1.0]], window: [[0.05, 1.3, -0.35], [0.25, 0.92, -0.75]], foot: [[-1.6, 1.4, -0.2], [0.0, 0.85, -0.75]] };
for (const v of (process.argv[2] || 'wide,eye').split(',')) {
  await p.evaluate(([pos, at]) => { const { camera, controls } = window.lapsim; camera.position.set(...pos); controls.target.set(...at); controls.update(); window.lapsim.stepSim(1); }, views[v]);
  await p.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  await p.screenshot({ path: `/tmp/or-${v}.png` });
}
console.log(JSON.stringify(await p.evaluate(() => window.lapsim.stats())));
await b.close();
