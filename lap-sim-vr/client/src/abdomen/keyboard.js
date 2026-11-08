// Mouse + keyboard tool-pose source (desktop testing without the tracker).
// Produces the same fulcrum poses as the physical tracker: the mouse moves the
// active instrument's TIP across the screen and the pivot angles are solved
// through its trocar (inverse kinematics), so the tool goes where you drag.
import { TOOL_TYPES, tipToPose, poseToShaft } from './tools.js';

const TOOL_IDS = { Digit1: 'left', Digit2: 'right', Digit3: 'assist', Digit0: 'scope' };
const TYPE_KEYS = { KeyZ: 'grasper', KeyX: 'maryland', KeyC: 'hook', KeyV: 'scissors', KeyB: 'clipper' };
const LABEL = { left: 'Left hand', right: 'Right hand', assist: 'Assistant', scope: 'Laparoscope' };

export const CONTROLS = [
  ['Click the image', 'focus the controls'],
  ['1 / 2 / 3 / 0 or Tab', 'select left hand · right hand · assistant · scope'],
  ['Drag (left mouse) or arrow keys', 'move the tool tip across the screen (pivots about its port)'],
  ['Mouse wheel or W / S', 'push in / pull out (insertion depth)'],
  ['Q / E', 'roll the instrument'],
  ['Hold Space or right mouse', 'close the jaws: grasp, clip, cut'],
  ['Hold F', 'energy (cautery, hook)'],
  ['Z X C V B or T', 'instrument: grasper · Maryland · hook · scissors · clip applier (T cycles)'],
  ['R', 'bring all tools back into view'],
  ['P', 'start / stop the autopilot demo'],
  ['H', 'hide / show this help'],
];

export class KeyboardTools {
  /**
   * @param abd Abdomen   @param el canvas   @param opts.requireShift mouse only with Shift held (OR view, where drag orbits)
   * @param opts.onToggleAutopilot () => void
   */
  constructor(abd, el, opts = {}) {
    Object.assign(this, { abd, el, opts });
    this.active = 'right'; this.down = new Set(); this.drag = null; this.jawHeld = false;
    this.lastAction = '';
    el.tabIndex = 0; el.style.outline = 'none';
    el.addEventListener('pointerdown', () => el.focus());
    addEventListener('keydown', (e) => this.key(e, true));
    addEventListener('keyup', (e) => this.key(e, false));
    addEventListener('blur', () => { this.down.clear(); this.jawHeld = false; });
    el.addEventListener('contextmenu', (e) => { if (this.mouseOk(e)) e.preventDefault(); });
    el.addEventListener('pointerdown', (e) => {
      if (!this.mouseOk(e)) return;
      if (e.button === 2) this.jawHeld = true; else if (e.button === 0) this.drag = { x: e.clientX, y: e.clientY };
      el.setPointerCapture(e.pointerId); e.stopImmediatePropagation(); this.touch();
    }, { capture: true });
    el.addEventListener('pointerup', (e) => { if (e.button === 2) this.jawHeld = false; else this.drag = null; });
    el.addEventListener('pointermove', (e) => {
      if (!this.drag) return;
      this.moveTip((e.clientX - this.drag.x) / el.clientWidth, (e.clientY - this.drag.y) / el.clientHeight);
      this.drag = { x: e.clientX, y: e.clientY };
    });
    el.addEventListener('wheel', (e) => {
      if (!this.mouseOk(e)) return;
      const p = this.pose(); p.depth = Math.max(0.02, Math.min(0.22, p.depth - Math.sign(e.deltaY) * 0.002));
      this.touch(); e.preventDefault(); e.stopImmediatePropagation();
    }, { passive: false, capture: true });
    this.readyPoses();
  }
  mouseOk(e) { return !this.opts.requireShift || e.shiftKey; }
  pose() { return this.abd.poses[this.active]; }
  touch() { this.abd.lastExternal = 0; this.used = true; }

  /** Hover every instrument ~1 cm in front of its target so it is visible on the monitor. */
  readyPoses() {
    const A = this.abd.anat, x = this.abd.sim.x, P = (i) => [x[i * 3], x[i * 3 + 1], x[i * 3 + 2]];
    const off = (p, d) => [p[0] + d[0], p[1] + d[1], p[2] + d[2]];
    const targets = {
      assist: off(P(A.landmarks.fundus), [0, -0.01, 0.012]),
      left: off(P(A.landmarks.infundibulum), [0.008, -0.004, 0.012]),
      right: off(A.duct.pos(Math.floor(A.duct.ids.length / 2)), [0.002, 0.01, 0.014]),
    };
    for (const [id, tip] of Object.entries(targets)) { const p = this.abd.poses[id]; Object.assign(p, tipToPose(p.port, tip), { jaw: 1, energy: false }); }
  }

  /** Move the active tool's tip by a fraction of the screen (camera-plane IK through the trocar). */
  moveTip(fx, fy) {
    const p = this.pose(); this.touch();
    if (this.active === 'scope') { p.yaw -= fx * 0.6; p.pitch -= fy * 0.4; return; }
    const cam = this.abd.camera.matrixWorld.elements, s = poseToShaft(p);
    const right = [cam[0], cam[1], cam[2]], up = [cam[4], cam[5], cam[6]];
    const dist = Math.hypot(...s.tip.map((v, k) => v - cam[12 + k]));
    const k = dist * 1.4; // screen fraction -> metres at the tip's distance (~70° FOV)
    const tip = s.tip.map((v, i) => v + right[i] * fx * k - up[i] * fy * k * 0.56);
    Object.assign(p, tipToPose(p.port, tip));
  }

  key(e, isDown) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    const handled = ['Space', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Tab'].includes(e.code);
    if (handled) e.preventDefault();
    if (isDown) this.down.add(e.code); else this.down.delete(e.code);
    if (!isDown || e.repeat) return;
    if (TOOL_IDS[e.code]) this.active = TOOL_IDS[e.code];
    if (e.code === 'Tab') { const ids = ['left', 'right', 'assist', 'scope']; this.active = ids[(ids.indexOf(this.active) + 1) % ids.length]; }
    if (this.active !== 'scope') {
      const p = this.pose();
      let t = TYPE_KEYS[e.code];
      if (e.code === 'KeyT') t = TOOL_TYPES[(TOOL_TYPES.indexOf(p.tool) + 1) % TOOL_TYPES.length];
      if (t) { p.tool = t; this.abd.setPoses([{ id: this.active, tool: t }], false); this.lastAction = `${LABEL[this.active]}: ${t}`; }
    }
    if (e.code === 'KeyR') { this.readyPoses(); this.lastAction = 'tools back in view'; }
    if (e.code === 'KeyP') this.opts.onToggleAutopilot?.();
    if (e.code === 'KeyH') this.opts.onToggleHelp?.();
    this.touch();
  }

  update(dt) {
    const d = this.down;
    if (!this.used && !d.size && !this.jawHeld) return;
    const p = this.pose(), m = dt * 0.35;
    const ax = (d.has('ArrowRight') ? 1 : 0) - (d.has('ArrowLeft') ? 1 : 0), ay = (d.has('ArrowDown') ? 1 : 0) - (d.has('ArrowUp') ? 1 : 0);
    if (ax || ay) this.moveTip(ax * m, ay * m);
    if (d.has('KeyW')) p.depth = Math.min(0.22, p.depth + dt * 0.03);
    if (d.has('KeyS')) p.depth = Math.max(0.02, p.depth - dt * 0.03);
    if (d.has('KeyQ')) p.roll -= dt * 2;
    if (d.has('KeyE')) p.roll += dt * 2;
    if (this.active !== 'scope') {
      const target = (d.has('Space') || this.jawHeld) ? 0 : 1;
      p.jaw += Math.sign(target - p.jaw) * Math.min(Math.abs(target - p.jaw), dt * 6);
      p.energy = d.has('KeyF');
    }
    if (d.size || this.jawHeld) this.touch();
  }

  /** Short status line for the on-screen help. */
  status() {
    const p = this.pose(), st = this.abd.state[this.active];
    if (this.active === 'scope') return `<b>${LABEL.scope}</b> · depth ${(p.depth * 100).toFixed(1)} cm`;
    return `<b>${LABEL[this.active]}</b> · ${p.tool} via ${p.port} · depth ${(p.depth * 100).toFixed(1)} cm · jaws ${p.jaw < 0.25 ? 'CLOSED' : 'open'}`
      + `${p.energy ? ' · <span style="color:#ffb020">ENERGY</span>' : ''}${st?.grabbed?.length ? ' · <span style="color:#7cf">holding tissue</span>' : ''}`;
  }
}

/** Collapsible on-screen cheat-sheet (+ optional live input display for recordings). */
export function controlsPanel({ keyTools, title = 'Instrument controls', extra = [], showInput = false }) {
  const el = document.createElement('div');
  el.id = 'controls-help';
  el.style.cssText = 'position:fixed;right:10px;bottom:10px;z-index:5;background:rgba(10,14,20,.72);color:#dfe7ef;font:11.5px/1.35 system-ui,sans-serif;border:1px solid #2a3542;border-radius:10px;width:min(330px,40vw);user-select:none';
  const rows = [...CONTROLS, ...extra].map(([k, v]) => `<tr><td style="padding:1px 8px 1px 0;color:#9fd0ff;vertical-align:top"><b>${k}</b></td><td>${v}</td></tr>`).join('');
  el.innerHTML = `<div id="ch-head" style="padding:7px 12px;cursor:pointer;display:flex;gap:8px;align-items:center"><b style="flex:1">🎮 ${title}</b><span id="ch-toggle" style="opacity:.7">hide ▾</span></div>
    <div id="ch-body" style="padding:0 12px 10px"><div id="ch-status" style="margin:0 0 6px;color:#fff"></div><table style="border-collapse:collapse">${rows}</table><div id="ch-last" style="margin-top:6px;color:#ffd27a"></div></div>`;
  document.body.appendChild(el);
  const body = el.querySelector('#ch-body'), tog = el.querySelector('#ch-toggle');
  const toggle = (force) => { const hide = typeof force === 'boolean' ? force : body.style.display !== 'none'; body.style.display = hide ? 'none' : ''; tog.textContent = hide ? 'show ▸ (H)' : 'hide ▾'; try { localStorage.setItem('lapsim-help-hidden', hide ? '1' : ''); } catch {} };
  try { if (localStorage.getItem('lapsim-help-hidden')) toggle(true); } catch {}
  el.querySelector('#ch-head').onclick = () => toggle();
  if (keyTools) keyTools.opts.onToggleHelp = toggle;
  let input = null;
  if (showInput) {
    input = document.createElement('div');
    input.style.cssText = 'position:fixed;left:12px;top:12px;z-index:5;background:rgba(0,0,0,.65);color:#fff;font:600 15px system-ui;padding:6px 10px;border-radius:8px;min-width:120px';
    document.body.appendChild(input);
    const dot = document.createElement('div');
    dot.style.cssText = 'position:fixed;width:18px;height:18px;margin:-9px 0 0 -9px;border:2px solid #fff;border-radius:50%;z-index:6;pointer-events:none;box-shadow:0 0 6px #000';
    document.body.appendChild(dot);
    addEventListener('pointermove', (e) => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px'; });
    addEventListener('pointerdown', () => { dot.style.background = 'rgba(255,255,255,.5)'; });
    addEventListener('pointerup', () => { dot.style.background = ''; });
  }
  return {
    update() {
      if (!keyTools) return;
      el.querySelector('#ch-status').innerHTML = keyTools.status();
      el.querySelector('#ch-last').textContent = keyTools.lastAction || '';
      if (input) {
        const keys = [...keyTools.down].map((c) => c.replace(/^Key|^Digit|^Arrow/, '')).join(' + ');
        input.textContent = `⌨ ${keys || '—'}${keyTools.drag ? '   🖱 drag' : ''}${keyTools.jawHeld ? '   🖱 right' : ''}`;
      }
    },
    note(text) { if (keyTools) keyTools.lastAction = text; },
  };
}
