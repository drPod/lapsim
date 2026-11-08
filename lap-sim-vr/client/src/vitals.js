// Anaesthesia patient monitor: HR / ECG, SpO2 / pleth, NIBP, EtCO2 / capnogram,
// drawn on a canvas texture with a real-monitor-style sweep. Values glide toward
// the targets set by `vitals` events, and alarms flash when out of range.
import * as THREE from 'three';

const W = 1024, H = 640;
const TRACE_X0 = 20, TRACE_X1 = 690;

// One cardiac cycle (phase 0..1) -> ECG mV-ish
function ecg(p) {
  const g = (mu, sig, a) => a * Math.exp(-((p - mu) ** 2) / (2 * sig * sig));
  return g(0.18, 0.025, 0.12) - g(0.285, 0.008, 0.12) + g(0.3, 0.01, 1.0) - g(0.318, 0.009, 0.25) + g(0.55, 0.05, 0.25);
}
function pleth(p) {
  const g = (mu, sig, a) => a * Math.exp(-((p - mu) ** 2) / (2 * sig * sig));
  return g(0.38, 0.09, 1.0) + g(0.62, 0.07, 0.35);
}
function capno(p) { // breath phase 0..1: exhale plateau then inhale drop
  if (p < 0.08) return p / 0.08;
  if (p < 0.5) return 1 + (p - 0.08) * 0.15;
  if (p < 0.56) return 1.06 * (1 - (p - 0.5) / 0.06);
  return 0;
}

export class VitalsMonitor {
  constructor({ width = 0.62 } = {}) {
    this.canvas = document.createElement('canvas');
    this.canvas.width = W; this.canvas.height = H;
    this.ctx = this.canvas.getContext('2d');
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.colorSpace = THREE.SRGBColorSpace;
    const h = width * H / W;
    this.group = new THREE.Group();
    this.group.name = 'vitals-monitor';
    const screen = new THREE.Mesh(new THREE.PlaneGeometry(width, h), new THREE.MeshBasicMaterial({ map: this.tex, toneMapped: false }));
    screen.position.z = 0.026;
    const body = new THREE.Mesh(new THREE.BoxGeometry(width + 0.06, h + 0.08, 0.05),
      new THREE.MeshStandardMaterial({ color: 0xd9dde2, roughness: 0.6 }));
    body.position.y = -0.01;
    this.group.add(body, screen);

    this.cur = { hr: 78, sys: 122, dia: 76, spo2: 99, rr: 12, etco2: 36, temp: 36.8 };
    this.tgt = { ...this.cur };
    this.rate = {}; // per-field glide speed
    this.phase = { heart: 0, breath: 0 };
    this.sweepX = TRACE_X0;
    this.prevY = {};
    this.listeners = new Set();
    this.lastDraw = 0;
    this.alarmText = '';
    this.ctx.fillStyle = '#000'; this.ctx.fillRect(0, 0, W, H);
  }

  onBeat(fn) { this.listeners.add(fn); }

  set(v) {
    const t = Math.max(0.05, (v.transitionMs ?? 3000) / 1000);
    const next = { ...v };
    if (typeof v.bp === 'string') { const [s, d] = v.bp.split('/').map(Number); next.sys = s; next.dia = d; }
    for (const k of Object.keys(this.cur)) {
      if (typeof next[k] === 'number') { this.tgt[k] = next[k]; this.rate[k] = Math.abs(next[k] - this.cur[k]) / t; }
    }
  }
  snap() { Object.assign(this.cur, this.tgt); }
  get map() { return Math.round((this.cur.sys + 2 * this.cur.dia) / 3); }

  alarms() {
    const a = [];
    const c = this.cur;
    if (c.spo2 < 90) a.push('SpO2 LOW');
    if (c.hr > 120) a.push('HR HIGH'); else if (c.hr < 50) a.push('HR LOW');
    if (c.sys < 90) a.push('NIBP LOW'); else if (c.sys > 180) a.push('NIBP HIGH');
    if (c.etco2 > 50) a.push('EtCO2 HIGH');
    return a;
  }

  update(dt, t) {
    for (const k of Object.keys(this.cur)) {
      const d = this.tgt[k] - this.cur[k];
      const step = (this.rate[k] || 5) * dt;
      this.cur[k] = Math.abs(d) <= step ? this.tgt[k] : this.cur[k] + Math.sign(d) * step;
    }
    // physiological jitter
    const hr = Math.max(1, this.cur.hr + Math.sin(t * 0.7) * 0.8);
    const prevHeart = this.phase.heart;
    this.phase.heart = (this.phase.heart + dt * hr / 60) % 1;
    if (this.phase.heart < prevHeart && this.cur.hr > 0) for (const fn of this.listeners) fn(this.cur);
    this.phase.breath = (this.phase.breath + dt * Math.max(0.01, this.cur.rr) / 60) % 1;

    // sweep the traces (screen speed 25 mm/s ≈ 140 px/s here)
    const ctx = this.ctx;
    const x0 = this.sweepX, x1 = x0 + 140 * dt;
    const traces = [
      { key: 'ecg', y: 120, amp: -80, color: '#33ff66', v: this.cur.hr > 0 ? ecg(this.phase.heart) : 0 },
      { key: 'pleth', y: 300, amp: -70 * Math.min(1, this.cur.spo2 / 95), color: '#33d6ff', v: pleth((this.phase.heart + 0.85) % 1) },
      { key: 'co2', y: 500, amp: -70 * Math.min(1.4, this.cur.etco2 / 40), color: '#ffe14d', v: capno(this.phase.breath) },
    ];
    const wrap = x1 >= TRACE_X1;
    const xa = x0, xb = Math.min(x1, TRACE_X1);
    ctx.fillStyle = '#000'; ctx.fillRect(xa + 1, 30, (wrap ? TRACE_X1 - xa : xb - xa) + 14, H - 60); // erase bar ahead
    ctx.lineWidth = 3; ctx.lineCap = 'round';
    for (const tr of traces) {
      const y = tr.y + tr.amp * tr.v;
      const py = this.prevY[tr.key] ?? y;
      ctx.strokeStyle = tr.color; ctx.beginPath(); ctx.moveTo(xa, py); ctx.lineTo(xb, y); ctx.stroke();
      this.prevY[tr.key] = y;
    }
    this.sweepX = wrap ? TRACE_X0 : x1;

    if (t - this.lastDraw > 1 / 20) { this.lastDraw = t; this.drawNumbers(t); this.tex.needsUpdate = true; }
  }

  drawNumbers(t) {
    const ctx = this.ctx, c = this.cur;
    const alarms = this.alarms();
    const flash = alarms.length && Math.floor(t * 2.5) % 2 === 0;
    ctx.fillStyle = '#000'; ctx.fillRect(TRACE_X1 + 14, 0, W - TRACE_X1 - 14, H); ctx.fillRect(0, 0, W, 30); ctx.fillRect(0, H - 30, W, 30);
    ctx.strokeStyle = '#333'; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(TRACE_X1 + 16, 30); ctx.lineTo(TRACE_X1 + 16, H - 30); ctx.stroke();
    // header bar
    ctx.font = '600 20px system-ui, sans-serif'; ctx.textBaseline = 'middle';
    ctx.fillStyle = alarms.length ? (flash ? '#ff2a2a' : '#7a0000') : '#1d2733';
    ctx.fillRect(0, 0, W, 30);
    ctx.fillStyle = '#fff'; ctx.textAlign = 'left';
    ctx.fillText(alarms.length ? `*** ${alarms.join('  ·  ')}` : 'ADULT  ·  OR 3  ·  LAP CHOLE', 12, 15);
    ctx.textAlign = 'right'; ctx.fillText(new Date().toTimeString().slice(0, 5), W - 12, 15);
    // trace labels
    ctx.textAlign = 'left'; ctx.font = '600 18px system-ui, sans-serif';
    ctx.fillStyle = '#33ff66'; ctx.fillText('II', 26, 46);
    ctx.fillStyle = '#33d6ff'; ctx.fillText('Pleth', 26, 226);
    ctx.fillStyle = '#ffe14d'; ctx.fillText('CO2', 26, 426);

    const big = (label, unit, value, color, y, size = 92, alarm = false) => {
      ctx.textAlign = 'left'; ctx.font = '600 20px system-ui, sans-serif';
      ctx.fillStyle = color; ctx.fillText(label, TRACE_X1 + 30, y);
      ctx.fillStyle = '#aaa'; ctx.fillText(unit, TRACE_X1 + 30, y + 24);
      ctx.textAlign = 'right'; ctx.font = `700 ${size}px system-ui, sans-serif`;
      if (alarm && flash) { ctx.fillStyle = '#ff2a2a'; ctx.fillRect(TRACE_X1 + 24, y - 14, W - TRACE_X1 - 34, size + 18); ctx.fillStyle = '#fff'; } else ctx.fillStyle = color;
      ctx.fillText(value, W - 18, y + size / 2 + 6);
    };
    big('HR', 'bpm', Math.round(c.hr), '#33ff66', 50, 100, c.hr > 120 || c.hr < 50);
    big('SpO2', '%', Math.round(c.spo2), '#33d6ff', 196, 100, c.spo2 < 90);
    big('NIBP', 'mmHg', `${Math.round(c.sys)}/${Math.round(c.dia)}`, '#ff5d5d', 342, 64, c.sys < 90 || c.sys > 180);
    ctx.textAlign = 'right'; ctx.font = '600 28px system-ui, sans-serif'; ctx.fillStyle = '#ff5d5d';
    ctx.fillText(`(${this.map})`, W - 18, 450);
    big('EtCO2', `mmHg · RR ${Math.round(c.rr)}`, Math.round(c.etco2), '#ffe14d', 478, 64, c.etco2 > 50);
    ctx.textAlign = 'left'; ctx.font = '500 18px system-ui, sans-serif'; ctx.fillStyle = '#ccc';
    ctx.fillText(`T ${c.temp.toFixed(1)} °C`, 12, H - 15);
  }
}
