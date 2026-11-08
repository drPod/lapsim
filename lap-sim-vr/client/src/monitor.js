// The laparoscopy monitor: the live box-camera feed as a video texture, with
// complication overlays (blood, cautery smoke, warning banner) composited in a
// single fragment shader so they cost almost nothing on the Quest GPU.
import * as THREE from 'three';

const vertexShader = /* glsl */ `
  varying vec2 vUv;
  void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;

const fragmentShader = /* glsl */ `
  uniform sampler2D map;
  uniform sampler2D hud;
  uniform float time;
  uniform float bleed;      // 0..1 visual blood level (eased)
  uniform float smoke;      // 0..1 visual smoke level (eased)
  uniform float hr;         // heart rate, drives arterial spurts
  uniform float pulsatile;  // 0/1
  uniform vec2 origin;      // bleed origin, GL uv (0,0 bottom-left)
  uniform float videoAspect;
  uniform float screenAspect;
  uniform float hasVideo;
  uniform float bannerBlink;
  uniform float bile;
  uniform vec2 bileOrigin;
  uniform float linearIn;
  uniform float exposure;
  varying vec2 vUv;

  float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
  float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), u.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), u.x), u.y);
  }
  float fbm(vec2 p) { float v = 0.0, a = 0.5; for (int i = 0; i < 5; i++) { v += a * noise(p); p *= 2.03; a *= 0.5; } return v; }

  vec3 fluid(vec3 col, vec2 uv, vec2 org, float level, float beat, vec3 thinC, vec3 thickC) {
    vec2 d = (uv - org) * vec2(screenAspect, 1.0);
    d.y *= d.y > 0.0 ? 1.5 : 0.8;  // gravity: pools spread more downward than upward
    float r = length(d);
    float spread = 0.06 + level * 0.8 + 0.035 * beat * level;
    vec2 w = vec2(fbm(d * 2.2 + vec2(time * 0.02, 0.0)), fbm(d * 2.2 + vec2(5.2, 1.3)));
    float edge = (fbm(d * 1.6 + w * 1.8 + vec2(0.0, -time * 0.04)) - 0.5) * (0.25 + 0.35 * level);
    float inside = spread - (r + edge);
    float pool = smoothstep(0.0, 0.025, inside);
    float cols = smoothstep(0.6, 0.66, fbm(vec2(uv.x * 7.0, 2.7)));
    float len = 0.15 + level * 0.85;
    float rivulet = cols * step(uv.y, org.y) * step(org.y - len, uv.y)
                  * smoothstep(0.2 + level * 0.5, 0.0, abs(uv.x - org.x))
                  * smoothstep(org.y - len, org.y - len + 0.08, uv.y);
    vec2 jd = (uv - org - vec2(0.04, 0.08) * beat) * vec2(screenAspect, 1.6);
    float jet = beat * smoothstep(0.04 + 0.12 * level, 0.0, length(jd));
    float mask = clamp(max(max(pool, rivulet * 0.95), jet), 0.0, 1.0);
    float thick = clamp(inside / 0.25, 0.0, 1.0);
    vec3 c = mix(thinC, thickC, thick) * (0.85 + 0.3 * fbm(uv * 5.0 + time * 0.05));
    float gloss = smoothstep(0.62, 0.75, fbm(uv * vec2(9.0, 5.0) + w + vec2(time * 0.03, time * 0.015))) * (0.3 + 0.7 * thick);
    float rim = smoothstep(0.0, 0.02, inside) - smoothstep(0.02, 0.06, inside);
    c += (gloss * 0.35 + rim * 0.25) * vec3(1.0, 0.85, 0.8);
    return mix(col, c, mask * (0.55 + 0.45 * smoothstep(0.0, 0.12, inside)));
  }

  // HDR (linear) sources such as the virtual abdomen: ACES filmic + sRGB here
  vec3 aces(vec3 x) { return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0); }
  vec3 sampleMap(vec2 uv) {
    vec3 c = texture2D(map, uv).rgb;
    if (linearIn > 0.5) c = pow(aces(c * exposure), vec3(1.0 / 2.2));
    return c;
  }

  // "cover" fit of the video into the 16:9 screen
  vec2 coverUv(vec2 uv) {
    float r = videoAspect / screenAspect;
    if (r > 1.0) uv.x = (uv.x - 0.5) / r + 0.5; else uv.y = (uv.y - 0.5) * r + 0.5;
    return uv;
  }

  void main() {
    vec2 uv = vUv;
    vec2 vuv = coverUv(uv);
    if (linearIn > 0.5) { // laparoscope optics: slight barrel distortion (sim render only)
      vec2 c = (vuv - 0.5) * vec2(screenAspect, 1.0);
      float r2 = dot(c, c);
      vuv = 0.5 + c * (1.0 - 0.07 * r2) / (1.0 - 0.07 * 0.6) / vec2(screenAspect, 1.0);
    }
    float s = smoke;
    vec2 drift = vec2(time * 0.02, time * 0.06);

    // heat shimmer under smoke
    vec2 disp = (vec2(fbm(uv * 5.0 + drift * 2.0), fbm(uv * 5.0 - drift * 2.0)) - 0.5) * 0.025 * s;
    vec3 col = sampleMap(vuv + disp).rgb;
    if (s > 0.01) { // cheap blur: smoke scatters light
      float o = 0.004 + 0.008 * s;
      vec3 b = sampleMap(vuv + disp + vec2(o, 0.0)).rgb + sampleMap(vuv + disp - vec2(o, 0.0)).rgb
             + sampleMap(vuv + disp + vec2(0.0, o)).rgb + sampleMap(vuv + disp - vec2(0.0, o)).rgb;
      col = mix(col, b * 0.25, clamp(s * 1.2, 0.0, 1.0));
    }
    if (hasVideo < 0.5) col = vec3(0.02, 0.03, 0.05) + 0.03 * noise(uv * 400.0 + time * 50.0);

    // ---------------- blood + bile (same fluid model, different colour / light absorption)
    if (bleed > 0.001) {
      float beat = pulsatile > 0.5 ? pow(0.5 + 0.5 * sin(time * 6.2831 * hr / 60.0), 8.0) : 0.0;
      col = fluid(col, uv, origin, bleed, beat, vec3(0.58, 0.05, 0.06), vec3(0.22, 0.0, 0.015));
      col *= mix(1.0, 0.6, bleed * bleed);          // blood absorbs the scope light
      col = mix(col, col * vec3(1.2, 0.55, 0.55), bleed * 0.5);
    }
    if (bile > 0.001) col = fluid(col, uv, bileOrigin, bile, 0.0, vec3(0.62, 0.62, 0.12), vec3(0.2, 0.32, 0.04));

    // ---------------- smoke / fog
    if (s > 0.01) {
      float f = fbm(uv * vec2(2.2, 3.0) + drift) * 1.2 + fbm(uv * 6.0 - drift * 1.7) * 0.35;
      float fog = clamp(smoothstep(0.25, 1.15, f * s * 1.15 + s * 0.3), 0.0, 0.88);
      col = mix(col, vec3(0.86, 0.86, 0.84), fog);
    }

    // scope vignette
    col *= smoothstep(1.35, 0.5, length((uv - 0.5) * vec2(1.7, 1.3)));

    // ---------------- HUD (banner, labels, captions drawn on a canvas)
    vec4 h = texture2D(hud, uv);
    float a = h.a * (uv.y > 0.84 ? bannerBlink : 1.0);
    col = mix(col, h.rgb, a);
    gl_FragColor = vec4(col, 1.0);
  }
`;

const HUD_W = 1280, HUD_H = 720;

export class LapMonitor {
  constructor({ width = 1.4 } = {}) {
    this.width = width;
    this.height = width * 9 / 16;
    this.group = new THREE.Group();
    this.group.name = 'lap-monitor';

    this.hudCanvas = document.createElement('canvas');
    this.hudCanvas.width = HUD_W; this.hudCanvas.height = HUD_H;
    this.hudCtx = this.hudCanvas.getContext('2d');
    this.hudTex = new THREE.CanvasTexture(this.hudCanvas);
    this.blank = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1); this.blank.needsUpdate = true;

    this.uniforms = {
      map: { value: this.blank }, hud: { value: this.hudTex }, time: { value: 0 },
      bleed: { value: 0 }, smoke: { value: 0 }, hr: { value: 78 }, pulsatile: { value: 1 },
      origin: { value: new THREE.Vector2(0.62, 0.62) }, videoAspect: { value: 16 / 9 }, screenAspect: { value: 16 / 9 },
      hasVideo: { value: 0 }, bannerBlink: { value: 1 },
      bile: { value: 0 }, bileOrigin: { value: new THREE.Vector2(0.5, 0.5) }, linearIn: { value: 0 }, exposure: { value: 1.0 },
    };
    const screen = new THREE.Mesh(
      new THREE.PlaneGeometry(this.width, this.height),
      new THREE.ShaderMaterial({ uniforms: this.uniforms, vertexShader, fragmentShader, toneMapped: false }),
    );
    screen.name = 'lap-screen';
    screen.position.z = 0.031;
    this.screen = screen;

    const bezelMat = new THREE.MeshStandardMaterial({ color: 0x15181c, roughness: 0.45, metalness: 0.3 });
    const bezel = new THREE.Mesh(new THREE.BoxGeometry(this.width + 0.06, this.height + 0.06, 0.06), bezelMat);
    const logo = new THREE.Mesh(new THREE.PlaneGeometry(0.06, 0.012), new THREE.MeshBasicMaterial({ color: 0x5aa0ff }));
    logo.position.set(0, -this.height / 2 - 0.016, 0.031);
    this.group.add(bezel, screen, logo);

    // target (from events) vs visual (eased) values
    this.target = { bleed: 0, smoke: 0, smokeUntil: 0, bile: 0 };
    this.visual = { bleed: 0, smoke: 0, bile: 0 };
    this.warning = null;
    this.sourceLabel = 'NO SIGNAL';
    this.drawHud();
  }

  setVideo(videoEl, label) {
    const tex = new THREE.VideoTexture(videoEl);
    tex.minFilter = THREE.LinearFilter; tex.magFilter = THREE.LinearFilter; tex.generateMipmaps = false;
    const old = this.uniforms.map.value;
    this.uniforms.map.value = tex; this.uniforms.linearIn.value = 0;
    if (old && old !== this.blank && old.isVideoTexture) old.dispose();
    const setAspect = () => { if (videoEl.videoWidth) this.uniforms.videoAspect.value = videoEl.videoWidth / videoEl.videoHeight; };
    setAspect(); videoEl.addEventListener('loadedmetadata', setAspect); videoEl.addEventListener('resize', setAspect);
    this.uniforms.hasVideo.value = 1;
    this.sourceLabel = label; this.drawHud();
  }

  /** Show a rendered texture (e.g. the virtual abdomen). linear = HDR input tonemapped in the shader. */
  setTexture(tex, label, { linear = true, aspect = 16 / 9 } = {}) {
    const old = this.uniforms.map.value;
    if (old && old !== this.blank && old.isVideoTexture) old.dispose();
    this.uniforms.map.value = tex; this.uniforms.videoAspect.value = aspect;
    this.uniforms.linearIn.value = linear ? 1 : 0; this.uniforms.hasVideo.value = 1;
    this.sourceLabel = label; this.drawHud();
  }

  setBile({ intensity, origin }) {
    this.target.bile = intensity;
    if (origin) this.uniforms.bileOrigin.value.set(origin.x, 1 - origin.y);
  }

  setTracker(text) { if (text !== this.trackerLabel) { this.trackerLabel = text; this.drawHud(); } }

  clearVideo(label = 'NO SIGNAL') { this.uniforms.hasVideo.value = 0; this.sourceLabel = label; this.drawHud(); }

  setBleed({ intensity, origin, pulsatile }) {
    this.target.bleed = intensity;
    if (origin) this.uniforms.origin.value.set(origin.x, 1 - origin.y);
    if (pulsatile !== undefined) this.uniforms.pulsatile.value = pulsatile ? 1 : 0;
  }
  setSmoke({ intensity = 0.7, durationMs = 8000 }) {
    this.target.smoke = intensity;
    this.target.smokeUntil = durationMs ? performance.now() + durationMs : Infinity;
  }
  setWarning(w) {
    this.warning = w ? { ...w, until: w.durationMs ? performance.now() + w.durationMs : Infinity } : null;
    this.drawHud();
  }
  setHeartRate(hr) { this.uniforms.hr.value = hr; }

  /** Immediately jump the eased values (for screenshots/tests). */
  snap() { this.visual.bleed = this.target.bleed; this.visual.smoke = this.target.smoke; this.visual.bile = this.target.bile; }

  update(dt, t) {
    const now = performance.now();
    if (now > this.target.smokeUntil) this.target.smoke = 0;
    if (this.warning && now > this.warning.until) this.setWarning(null);
    const ease = (cur, tgt, up, down) => (tgt > cur ? Math.min(tgt, cur + up * dt) : Math.max(tgt, cur - down * dt));
    this.visual.bleed = ease(this.visual.bleed, this.target.bleed, 0.22, 0.08); // fills fast, suctions slowly
    this.visual.smoke = ease(this.visual.smoke, this.target.smoke, 1.2, 0.18);
    this.visual.bile = ease(this.visual.bile, this.target.bile, 0.18, 0.08);
    this.uniforms.bile.value = this.visual.bile;
    this.uniforms.bleed.value = this.visual.bleed;
    this.uniforms.smoke.value = this.visual.smoke;
    this.uniforms.time.value = t;
    const lvl = this.warning?.level;
    this.uniforms.bannerBlink.value = lvl === 'critical' ? 0.55 + 0.45 * (Math.sin(t * 6) > 0 ? 1 : 0.4) : 1;
    // clock in the corner, once a second
    const sec = Math.floor(t);
    if (sec !== this._lastSec) { this._lastSec = sec; this.drawHud(); }
  }

  drawHud() {
    const c = this.hudCtx;
    c.clearRect(0, 0, HUD_W, HUD_H);
    c.textBaseline = 'middle';
    // source label + clock (like a real camera control unit overlay)
    c.font = '600 26px system-ui, sans-serif';
    c.fillStyle = 'rgba(0,0,0,0.45)'; c.fillRect(16, HUD_H - 54, 360, 38);
    c.fillStyle = this.sourceLabel.startsWith('LIVE') || this.sourceLabel.startsWith('SIM') ? '#3ddc84' : this.sourceLabel.startsWith('DEMO') ? '#ffb020' : '#ff6b6b';
    c.beginPath(); c.arc(36, HUD_H - 35, 8, 0, Math.PI * 2); c.fill();
    c.fillStyle = '#e8eef5'; c.fillText(this.sourceLabel, 54, HUD_H - 35);
    const d = new Date();
    c.font = '500 24px ui-monospace, monospace'; c.textAlign = 'right';
    c.fillStyle = 'rgba(0,0,0,0.45)'; c.fillRect(HUD_W - 190, HUD_H - 54, 174, 38);
    c.fillStyle = '#e8eef5'; c.fillText(d.toTimeString().slice(0, 8), HUD_W - 30, HUD_H - 35);
    c.textAlign = 'left';
    if (this.trackerLabel) { // box tracker streaming
      c.font = '700 24px system-ui, sans-serif'; const w = c.measureText(this.trackerLabel).width + 44;
      c.fillStyle = 'rgba(0,0,0,0.5)'; c.fillRect(HUD_W - w - 16, HUD_H - 100, w, 36);
      c.fillStyle = '#3ddc84'; c.beginPath(); c.arc(HUD_W - w + 4, HUD_H - 82, 7, 0, 7); c.fill();
      c.fillStyle = '#e8eef5'; c.fillText(this.trackerLabel, HUD_W - w + 18, HUD_H - 82);
    }
    if (this.uniforms.hasVideo.value < 0.5) {
      c.font = '700 64px system-ui, sans-serif'; c.textAlign = 'center'; c.fillStyle = 'rgba(255,255,255,0.7)';
      c.fillText(this.sourceLabel === 'NO SIGNAL' ? 'NO SIGNAL' : 'CONNECTING…', HUD_W / 2, HUD_H / 2); c.textAlign = 'left';
    }
    if (this.warning) {
      const colors = { critical: ['#c4141c', '#fff'], warning: ['#e08a00', '#111'], info: ['#1f6feb', '#fff'] };
      const [bg, fg] = colors[this.warning.level || 'critical'];
      c.fillStyle = bg; c.fillRect(0, 0, HUD_W, 108);
      c.fillStyle = 'rgba(0,0,0,0.25)'; c.fillRect(0, 100, HUD_W, 8);
      c.fillStyle = fg; c.textAlign = 'center';
      let size = 56; c.font = `800 ${size}px system-ui, sans-serif`;
      const text = (this.warning.level === 'info' ? '' : '⚠ ') + this.warning.text.toUpperCase();
      while (c.measureText(text).width > HUD_W - 60 && size > 24) { size -= 2; c.font = `800 ${size}px system-ui, sans-serif`; }
      c.fillText(text, HUD_W / 2, 54); c.textAlign = 'left';
    }
    this.hudTex.needsUpdate = true;
  }
}
