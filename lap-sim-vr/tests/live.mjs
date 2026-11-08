// Checks the live path: MediaMTX (with something publishing to path "cam") ->
// server WHEP proxy -> MediaMTX reader.js -> video texture on the VR monitor.
//   npm run mediamtx &  (+ a publisher, e.g. sender.html or ffmpeg)  then  node tests/live.mjs
import fs from 'node:fs';
import { chromium } from 'playwright';

const BASE = process.env.BASE_URL || 'http://localhost:8787';
const OUT = process.env.OUT || 'artifacts';
fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({
  executablePath: process.env.CHROME || (fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined),
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', '--autoplay-policy=no-user-gesture-required'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await page.goto(`${BASE}/?emulate=1&clean=1&controllers=0&source=auto`);
await page.waitForFunction(() => window.lapsim?.monitor.sourceLabel.startsWith('LIVE'), null, { timeout: 30000 });
await page.click('#vr-button');
await page.waitForFunction(() => window.lapsim.renderer.xr.isPresenting);
await page.evaluate(() => {
  const { THREE, device } = window.lapsim;
  const m = new THREE.Matrix4().lookAt(new THREE.Vector3(0, 1.65, 0.05), new THREE.Vector3(0.3, 1.64, -1.65), new THREE.Vector3(0, 1, 0));
  const q = new THREE.Quaternion().setFromRotationMatrix(m);
  device.position.set(0, 1.65, 0.05); device.quaternion.set(q.x, q.y, q.z, q.w);
});
await page.waitForTimeout(2500);
await page.screenshot({ path: `${OUT}/09-live-webrtc-feed.png` });
const stats = await page.evaluate(() => { const v = window.lapsim.video.live; return { label: window.lapsim.monitor.sourceLabel, w: v.videoWidth, h: v.videoHeight, t: v.currentTime }; });
console.log('live OK', JSON.stringify(stats));
await browser.close();
