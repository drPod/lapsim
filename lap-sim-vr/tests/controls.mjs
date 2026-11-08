// Drives the virtual instruments in the scope view ONLY through real mouse and
// keyboard events (like a person at a laptop) and checks that they grasp, burn,
// clip and cut. Saves frames for a GIF: artifacts/ctl-frames/*.png
//   npm run build && npm start &   then   node tests/controls.mjs
import fs from 'node:fs';
import { chromium } from 'playwright';

const BASE = process.env.BASE_URL || 'http://localhost:8787';
const dir = `${process.env.OUT || 'artifacts'}/ctl-frames`; fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
await fetch(`${BASE}/api/events`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"type":"reset"}' });
const browser = await chromium.launch({ executablePath: fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const W = 1100, H = 620;
const page = await browser.newPage({ viewport: { width: W, height: H } });
page.on('pageerror', (e) => console.log('pageerror', e.message));
await page.goto(`${BASE}/?view=scope&manual=1&simRes=960x540&showInput=1`);
await page.waitForFunction(() => window.lapsim?.abd?.ready && document.getElementById('controls-help'), null, { timeout: 120000 });

let frame = 0;
const step = async (n = 3, shots = 1) => {
  for (let i = 0; i < shots; i++) {
    await page.evaluate((m) => window.lapsim.stepSim(m), n);
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    await page.screenshot({ path: `${dir}/${String(frame++).padStart(4, '0')}.png` });
  }
};
const info = (target) => page.evaluate((target) => {
  const L = window.lapsim, abd = L.abd;
  const { THREE } = L, id = window.__active;
  const poseTip = (p) => { const k = window.__tools.poseToShaft(p); return k.tip; };
  const tip = poseTip(abd.poses[id]);
  const proj = (p) => { const v = new THREE.Vector3(...p).project(abd.camera); return [(v.x + 1) / 2, (1 - v.y) / 2]; };
  const tgt = target ? (target === 'fat' ? abd.anat.duct.pos(Math.floor(abd.anat.duct.ids.length / 2)).map((v, i) => v + [0, 0.004, 0.003][i]) : window.__targets[target]()) : tip;
  return { tip, tipS: proj(tip), tgt, tgtS: proj(tgt), d: Math.hypot(...tip.map((v, i) => v - tgt[i])), grabbed: abd.state[id]?.grabbed.length || 0, stats: abd.stats };
}, target);
await page.evaluate(() => {
  const abd = window.lapsim.abd;
  window.__tools = { poseToShaft: window.lapsim.poseToShaft };
  const A = abd.anat, n = A.duct.ids.length;
  window.__targets = { ductGB: () => A.duct.pos(3), ductCBD: () => A.duct.pos(n - 4), ductCBD2: () => A.duct.pos(n - 6), ductCut: () => { const c = A.duct.clips.map((k) => k.idx).sort((a, b) => a - b); return A.duct.pos(c.length >= 2 ? Math.round((c[0] + c[c.length - 1]) / 2) : Math.round(n / 2)); },
    fatNode: () => { const m = A.duct.pos(Math.floor(n / 2)).map((v, i) => v + [0, 0.004, 0.003][i]); const x = abd.sim.x; let best = null, bd = 1; for (const k of A.sheet.ids) { const d = Math.hypot(x[k * 3] - m[0], x[k * 3 + 1] - m[1], x[k * 3 + 2] - m[2]); if (d < bd && A.sheet.mesh.geometry.index.array.includes(A.sheet.local.get(k))) { bd = d; best = k; } } return [x[best * 3], x[best * 3 + 1], x[best * 3 + 2]]; },
    infund: () => { const x = abd.sim.x, i = A.landmarks.infundibulum; return [x[i * 3], x[i * 3 + 1], x[i * 3 + 2]]; } };
});
const C = { x: W / 2, y: H / 2 };
await page.mouse.move(C.x, C.y);
await page.mouse.click(C.x, C.y); // focus
const key = async (k) => { await page.keyboard.press(k); await step(2); };
const select = async (id, k) => { await page.evaluate((id) => { window.__active = id; }, id); await key(k); };

/** Aim like a person: drag the tip onto the target on screen, then wheel in until it's there. */
async function aim(target, standoff = 0.0015) {
  for (let it = 0; it < 24; it++) {
    const s = await info(target);
    const dx = (s.tgtS[0] - s.tipS[0]) * W, dy = (s.tgtS[1] - s.tipS[1]) * H;
    if (Math.hypot(dx, dy) > 6) {
      await page.mouse.move(C.x, C.y); await page.mouse.down();
      await page.mouse.move(C.x + dx * 0.8, C.y + dy * 0.8, { steps: 4 }); await page.mouse.up();
      await step(2);
    } else if (s.d > standoff + 0.002) {
      const before = s.d;
      await page.mouse.wheel(0, -100); await step(1);
      const after = (await info(target)).d;
      if (after > before) { await page.mouse.wheel(0, 100); await step(1); break; }
    } else break;
  }
  return info(target);
}
const hold = async (k, frames) => { await page.keyboard.down(k); await step(3, frames); await page.keyboard.up(k); await step(2); };
const results = {};

await step(2, 3);
// 1) left hand: grasp the infundibulum and retract it laterally
await select('left', 'Digit1');
let s = await aim('infund'); console.log('left at infundibulum, d =', (s.d * 1000).toFixed(1), 'mm');
await page.keyboard.down('Space'); await step(3, 2);
results.leftGrab = (await info()).grabbed;
await page.mouse.move(C.x, C.y); await page.mouse.down(); await page.mouse.move(C.x + 110, C.y + 30, { steps: 6 }); await page.mouse.up(); await step(3, 3);
// keep holding the infundibulum (jaws stay closed while Space is held)
// 2) right hand: Maryland grabs the peritoneal veil and pulls (tears it)
await page.keyboard.up('Space');
await page.evaluate(() => { const st = window.lapsim.abd.state.left; window.__leftHold = st.grabbed.length; window.lapsim.abd.poses.left.jaw = 0; });
await select('right', 'Digit2'); await key('KeyX');
s = await aim('fatNode'); console.log('maryland at veil, d =', (s.d * 1000).toFixed(1), 'mm');
const tris0 = await page.evaluate(() => window.lapsim.abd.anat.sheet?.alive);
await page.keyboard.down('Space'); await step(3, 2);
results.marylandGrab = (await info()).grabbed;
await page.mouse.move(C.x, C.y); await page.mouse.down(); await page.mouse.move(C.x - 60, C.y - 140, { steps: 8 }); await page.mouse.up();
await page.mouse.wheel(0, 500); await step(3, 5);
await page.keyboard.up('Space'); await step(3, 2);
results.veilTornTris = tris0 - await page.evaluate(() => window.lapsim.abd.anat.sheet?.alive);
// 3) hook + energy on the remaining veil
await key('KeyC');
s = await aim('fat', 0.001); console.log('hook at veil, d =', (s.d * 1000).toFixed(1), 'mm');
const smokeBefore = await page.evaluate(() => window.lapsim.abd.lastSmoke);
await hold('KeyF', 6);
results.burned = await page.evaluate((b) => window.lapsim.abd.lastSmoke !== b, smokeBefore);
// 4) clip applier: one clip near the gallbladder, two on the CBD side
await key('KeyB');
for (const t of ['ductGB', 'ductCBD', 'ductCBD2']) {
  s = await aim(t, 0.0005); console.log('clipper at', t, 'd =', (s.d * 1000).toFixed(1), 'mm');
  await hold('Space', 2);
}
results.clips = (await info()).stats.clips;
// 5) scissors: divide the duct between the clips
await key('KeyV');
s = await aim('ductCut', 0.0005); console.log('scissors at duct, d =', (s.d * 1000).toFixed(1), 'mm');
console.log('scissor debug', await page.evaluate(() => { const a = window.lapsim.abd, sh = window.lapsim.poseToShaft(a.poses.right); const h = a.nearestRod(sh.tip, 0.006); return JSON.stringify({ tool: a.poses.right.tool, jaw: a.poses.right.jaw, closed: a.state.right.closed, hit: h && { rod: h.rod.name, i: h.i, d: +(h.d * 1000).toFixed(1), seg: a.segmentAt(h.rod, sh.tip), clips: h.rod.clips.map((c) => c.idx), cuts: h.rod.cuts } }); }));
await hold('Space', 3);
results.cuts = (await info()).stats.cuts;
await page.mouse.wheel(0, 400); await step(3, 6);
console.log('RESULTS', JSON.stringify(results));
await browser.close();
