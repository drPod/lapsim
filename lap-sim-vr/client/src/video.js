// Laparoscope feed: live WebRTC (WHEP) from MediaMTX — Pi camera or laptop
// webcam — with automatic fallback to a looping demo clip when no camera is up.
// WHEP signalling uses MediaMTX's own reader.js (vendored in public/vendor).

const DEMO_URL = './media/demo-lap-chole.mp4';

function makeVideo() {
  const v = document.createElement('video');
  v.muted = true; v.playsInline = true; v.crossOrigin = 'anonymous'; v.autoplay = true;
  v.setAttribute('playsinline', ''); v.setAttribute('muted', '');
  return v;
}

export class VideoSource {
  /**
   * @param {object} o
   * @param {(video: HTMLVideoElement, label: string) => void} o.onVideo
   * @param {(mode: string, detail: string) => void} o.onStatus
   */
  constructor({ onVideo, onStatus }) {
    this.onVideo = onVideo; this.onStatus = onStatus;
    this.demo = makeVideo(); this.demo.loop = true; this.demo.src = DEMO_URL;
    this.live = makeVideo();
    this.reader = null;
    this.mode = 'auto';
    this.active = null; // 'live' | 'demo'
    this.whep = null;
  }

  async configure({ mode, whep }) {
    this.mode = mode || 'auto';
    if (whep) this.whep = new URL(whep, location.href).toString();
    if (this.mode === 'demo') { this.stopLive(); this.useDemo('DEMO · sample lap chole'); return; }
    this.startLive();
    if (this.mode === 'auto' && this.active !== 'live') this.useDemo('DEMO · waiting for camera');
    if (this.mode === 'live' && this.active !== 'live') this.onStatus('connecting', `connecting to ${this.whep}`);
  }

  useDemo(label) {
    this.active = 'demo';
    this.demo.play().catch(() => {});
    this.onVideo(this.demo, label);
    this.onStatus('demo', label);
  }

  startLive() {
    if (this.reader) return;
    if (!window.MediaMTXWebRTCReader) { this.onStatus('error', 'reader.js not loaded'); return; }
    this.reader = new window.MediaMTXWebRTCReader({
      url: this.whep,
      onError: (err) => {
        if (this.active === 'live') { this.active = null; if (this.mode !== 'live') this.useDemo('DEMO · camera lost'); }
        this.onStatus(this.active === 'demo' ? 'demo' : 'error', `camera: ${err}`);
      },
      onTrack: (evt) => {
        if (evt.track.kind !== 'video') return;
        this.live.srcObject = evt.streams[0];
        this.live.play().catch(() => {});
        this.active = 'live';
        this.demo.pause();
        this.onVideo(this.live, 'LIVE · box camera');
        this.onStatus('live', 'LIVE · box camera (WebRTC)');
      },
    });
  }

  stopLive() {
    if (this.reader) { this.reader.close(); this.reader = null; }
    this.live.srcObject = null;
    if (this.active === 'live') this.active = null;
  }

  /** Re-kick playback after a user gesture (Quest may block autoplay before one). */
  resume() { (this.active === 'live' ? this.live : this.demo).play().catch(() => {}); }
}
