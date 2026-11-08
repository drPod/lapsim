// OR sounds: pulse-oximeter beep (pitch falls with SpO2, like real monitors),
// alarm tones, and OR staff voices via speechSynthesis (or an agent-supplied
// audio clip). Plus a caption panel so speech is readable in the headset.
import * as THREE from 'three';

export class ORAudio {
  constructor() {
    this.ctx = null;
    this.muted = false;
    this.lastAlarm = 0;
  }
  /** Must be called from a user gesture (Enter VR / click). */
  unlock() {
    if (!this.ctx) this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    if (this.ctx.state === 'suspended') this.ctx.resume();
  }
  tone(freq, dur = 0.08, gain = 0.06, type = 'sine', when = 0) {
    if (!this.ctx || this.muted || this.ctx.state !== 'running') return;
    const t = this.ctx.currentTime + when;
    const o = this.ctx.createOscillator(), g = this.ctx.createGain();
    o.type = type; o.frequency.value = freq;
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(gain, t + 0.005); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(this.ctx.destination); o.start(t); o.stop(t + dur + 0.02);
  }
  beat(v) {
    // ~880 Hz at 100 %, dropping ~1 semitone per % below
    const f = 880 * Math.pow(2, -(100 - Math.min(100, v.spo2)) / 12);
    this.tone(f, 0.09, 0.05, 'sine');
    const now = performance.now();
    if (now - this.lastAlarm > 4000 && (v.spo2 < 90 || v.hr > 120 || v.hr < 50 || v.sys < 90)) {
      this.lastAlarm = now; // IEC 60601-1-8 style high-priority burst: 3 + 2 pulses
      [0, 0.15, 0.3, 0.7, 0.85].forEach((w) => this.tone(988, 0.12, 0.07, 'square', w));
    }
  }
  say({ speaker, text, audioUrl, voice }) {
    if (this.muted) return;
    if (audioUrl) { new Audio(audioUrl).play().catch(() => {}); return; }
    if (!('speechSynthesis' in window)) return;
    const u = new SpeechSynthesisUtterance(text);
    const voices = speechSynthesis.getVoices();
    const hash = [...speaker].reduce((a, ch) => a + ch.charCodeAt(0), 0);
    const en = voices.filter((v) => v.lang?.startsWith('en'));
    u.voice = voices.find((v) => voice && v.name.includes(voice)) || en[hash % Math.max(1, en.length)] || null;
    u.pitch = 0.8 + (hash % 5) * 0.1; u.rate = 1.05;
    speechSynthesis.speak(u);
  }
}

/** Caption strip under the main monitor showing the last few lines of OR dialogue. */
export class Captions {
  constructor({ width = 1.4 } = {}) {
    this.canvas = document.createElement('canvas');
    this.canvas.width = 1400; this.canvas.height = 200;
    this.ctx = this.canvas.getContext('2d');
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.colorSpace = THREE.SRGBColorSpace;
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(width, width * 200 / 1400),
      new THREE.MeshBasicMaterial({ map: this.tex, transparent: true, toneMapped: false, depthWrite: false }));
    this.mesh.name = 'captions';
    this.lines = [];
    this.draw();
  }
  push(speaker, text) {
    this.lines.push({ speaker, text, at: performance.now() });
    this.lines = this.lines.slice(-2);
    this.draw();
  }
  clear() { this.lines = []; this.draw(); }
  update() {
    const n = this.lines.length;
    this.lines = this.lines.filter((l) => performance.now() - l.at < 12000);
    if (n !== this.lines.length) this.draw();
  }
  draw() {
    const c = this.ctx;
    c.clearRect(0, 0, 1400, 200);
    if (!this.lines.length) { this.tex.needsUpdate = true; return; }
    c.fillStyle = 'rgba(8,12,18,0.78)';
    c.beginPath(); c.roundRect(0, 0, 1400, 200, 24); c.fill();
    c.textBaseline = 'middle';
    this.lines.forEach((l, i) => {
      const y = this.lines.length === 1 ? 100 : 55 + i * 90;
      c.font = '700 40px system-ui, sans-serif'; c.fillStyle = '#7cc4ff';
      const label = `${l.speaker}: `;
      c.fillText(label, 30, y);
      const lw = c.measureText(label).width;
      c.font = '500 40px system-ui, sans-serif'; c.fillStyle = '#fff';
      let text = l.text;
      while (c.measureText(text).width > 1340 - lw && text.length > 4) text = text.slice(0, -2);
      if (text !== l.text) text = text.slice(0, -1) + '…';
      c.fillText(text, 30 + lw, y);
    });
    this.tex.needsUpdate = true;
  }
}
