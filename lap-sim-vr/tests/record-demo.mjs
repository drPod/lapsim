// Records the director panel (left) driving the emulated-Quest VR view (right).
//   node tests/record-demo.mjs   -> artifacts/demo.webm   (server must be running)
import fs from 'node:fs';
import { chromium } from 'playwright';

const BASE = process.env.BASE_URL || 'http://localhost:8787';
const OUT = process.env.OUT || 'artifacts';
fs.mkdirSync(OUT, { recursive: true });
const W = 1600, H = 720;
const browser = await chromium.launch({
  executablePath: process.env.CHROME || (fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined),
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--autoplay-policy=no-user-gesture-required'],
});
const ctx = await browser.newContext({ viewport: { width: W, height: H }, recordVideo: { dir: `${OUT}/rec`, size: { width: W, height: H } } });
const page = await ctx.newPage();
await page.goto(`${BASE}/api/config`); // same origin as the iframes (IWER needs their localStorage)
await page.setContent(`<body style="margin:0;display:flex;background:#000">
  <iframe id="ctl" src="${BASE}/control.html" style="width:600px;height:${H}px;border:0;border-right:2px solid #333"></iframe>
  <iframe id="vr" allow="xr-spatial-tracking; autoplay" src="${BASE}/?emulate=1&clean=1&controllers=0" style="width:${W - 602}px;height:${H}px;border:0"></iframe></body>`);
await page.waitForTimeout(1000);
const ctl = page.frame({ url: /control\.html/ });
const vr = page.frame({ url: /emulate=1/ });
console.log('frames', !!ctl, !!vr);
await vr.waitForFunction(() => window.lapsim?.bus.connected && window.lapsim.scene.getObjectByName('room'), null, { timeout: 30000 });
await ctl.waitForFunction(() => document.getElementById('conn').textContent === 'connected');
await ctl.addStyleTag({ content: 'button.flash{outline:4px solid #fff;transform:scale(1.08);transition:transform .1s}' });
await page.waitForTimeout(2000);
console.log('entering VR');
await vr.click('#vr-button');
await vr.waitForFunction(() => window.lapsim.renderer.xr.isPresenting);
await vr.evaluate(() => {
  const { THREE, device } = window.lapsim;
  const p = new THREE.Vector3(0.15, 1.66, 0.1);
  const m = new THREE.Matrix4().lookAt(p, new THREE.Vector3(0.55, 1.6, -1.65), new THREE.Vector3(0, 1, 0));
  const q = new THREE.Quaternion().setFromRotationMatrix(m);
  device.position.set(p.x, p.y, p.z); device.quaternion.set(q.x, q.y, q.z, q.w);
});
await ctl.click('[data-ev*="reset"]');
await page.waitForTimeout(1500);

async function press(selector, wait) {
  const el = ctl.locator(selector).first();
  // dispatch directly: Playwright's actionability checks crawl while swiftshader renders WebGL
  await el.evaluate((b) => { b.scrollIntoView({ block: 'center' }); b.classList.add('flash'); setTimeout(() => b.classList.remove('flash'), 700); b.click(); });
  console.log('pressed', selector);
  await page.waitForTimeout(wait);
}
const t0 = Date.now();
await press('button:has-text("Arterial")', 3000);
await press('button:has-text("Hemorrhagic shock")', 800);
await press('#warnGo', 800);
await press('button:has-text("Pressure dropping")', 3500);
await press('button:has-text("Stop / suction")', 1200);
await press('button:has-text("Cautery puff")', 3500);
console.log('interaction window', Date.now() - t0, 'ms');
const video = page.video();
await ctx.close();
fs.renameSync(await video.path(), `${OUT}/demo.webm`);
console.log('saved', `${OUT}/demo.webm`);
await browser.close();
