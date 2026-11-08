// External frame stream for the lap monitor (e.g. the AI-restyled box camera from the
// live-restyle service): a "latest frame" JPEG/PNG URL that is re-fetched when a new
// frame is announced, with a crossfade between frames.
//
// New-frame notifications (any of these):
//  - our event bus: {type:"frame", url?, seq?}  (live-restyle can POST it to /api/events)
//  - a WebSocket of the frame service (cfg.ws): any message = "new frame"; a JSON message
//    with a `url` field switches the URL
//  - polling fallback every cfg.pollMs (default 15 s; 0 = off)
// Cross-origin images go through the server's /api/frame-proxy so WebGL can use them.
import * as THREE from 'three';

const W = 1280, H = 720;

export class FrameStream {
  constructor({ onTexture, onStatus } = {}) {
    Object.assign(this, { onTexture, onStatus });
    this.canvas = document.createElement('canvas'); this.canvas.width = W; this.canvas.height = H;
    this.ctx = this.canvas.getContext('2d');
    this.ctx.fillStyle = '#05070a'; this.ctx.fillRect(0, 0, W, H);
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.colorSpace = THREE.NoColorSpace; // monitor shader treats video/frames as display-referred
    this.prev = null; this.next = null; this.fadeT = 1; this.fadeDur = 0.08;
    this.cfg = { url: '', ws: '', pollMs: 15000 };
    this.frames = 0; this.lastFrameAt = 0;
  }
  configure(cfg) {
    this.stop();
    this.cfg = { ...this.cfg, ...cfg };
    this.active = true;
    if (this.cfg.ws) this.openWs();
    if (this.cfg.pollMs > 0) this.poll = setInterval(() => this.refresh('poll'), this.cfg.pollMs);
    this.refresh('start');
    this.onTexture?.(this.texture);
  }
  stop() {
    this.active = false;
    clearInterval(this.poll); this.poll = null;
    if (this.ws) { this.ws.onclose = null; this.ws.close(); this.ws = null; }
  }
  openWs() {
    try {
      this.ws = new WebSocket(this.cfg.ws);
      this.ws.onmessage = (m) => { let url; try { url = JSON.parse(m.data).url; } catch { /* plain ping */ } this.refresh('ws', url); };
      this.ws.onclose = () => { if (this.active) setTimeout(() => this.active && this.openWs(), 3000); };
    } catch (e) { this.onStatus?.('error', `frame ws: ${e.message}`); }
  }
  /** Called for {type:"frame"} bus events and by the ws/poll triggers. */
  refresh(why, url) {
    if (!this.active) return;
    if (url) this.cfg.url = url;
    if (!this.cfg.url) { this.onStatus?.('error', 'no frame URL set'); return; }
    const u = new URL(this.cfg.url, location.href);
    const same = u.origin === location.origin;
    const src = same ? `${u.href}${u.search ? '&' : '?'}_t=${Date.now()}` : `./api/frame-proxy?url=${encodeURIComponent(u.href)}&_t=${Date.now()}`;
    const img = new Image(); img.crossOrigin = 'anonymous';
    img.onload = () => { this.prev = this.next; this.next = img; this.fadeT = this.prev ? 0 : 1; this.frames++; this.lastFrameAt = Date.now(); this.draw(); this.onStatus?.('live', `AI frames · ${this.frames} received (${why})`); };
    img.onerror = () => this.onStatus?.('error', `frame load failed: ${this.cfg.url}`);
    img.src = src;
  }
  cover(img, alpha) {
    const r = Math.max(W / img.naturalWidth, H / img.naturalHeight), w = img.naturalWidth * r, h = img.naturalHeight * r;
    this.ctx.globalAlpha = alpha; this.ctx.drawImage(img, (W - w) / 2, (H - h) / 2, w, h); this.ctx.globalAlpha = 1;
  }
  draw() {
    const e = this.fadeT * this.fadeT * (3 - 2 * this.fadeT);
    if (this.prev && e < 1) this.cover(this.prev, 1);
    if (this.next) this.cover(this.next, this.prev ? e : 1);
    this.texture.needsUpdate = true;
  }
  update(dt) { if (this.fadeT < 1) { this.fadeT = Math.min(1, this.fadeT + dt / this.fadeDur); this.draw(); } }
}
