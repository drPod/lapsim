// lap-sim-vr event server.
//  - WebSocket hub on /ws: director panel, future AI agents and hardware daemons
//    send JSON events, every connected client receives them.
//  - REST: POST /api/events, GET /api/state, GET /api/schema, GET /api/config
//  - /mediamtx/* reverse proxy so the HTTPS page can do WHEP/WHIP signalling with
//    a plain-HTTP MediaMTX on the LAN (avoids mixed-content blocking).
//  - Serves the built client (dist/) in production.
import fs from 'node:fs';
import dgram from 'node:dgram';
import http from 'node:http';
import https from 'node:https';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import express from 'express';
import { WebSocketServer } from 'ws';
import { Ajv } from 'ajv';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = Number(process.env.PORT || 8787);
const HOST = process.env.HOST || '0.0.0.0';
const USE_HTTPS = /^(1|true|yes)$/i.test(process.env.HTTPS || '');
const MEDIAMTX_URL = (process.env.MEDIAMTX_URL || 'http://127.0.0.1:8889').replace(/\/$/, '');
const CAMERA_PATH = process.env.CAMERA_PATH || 'cam';
const DEFAULT_SOURCE = process.env.DEFAULT_SOURCE || (process.env.RESTYLE_URL ? 'frames' : 'sim'); // sim | auto | live | demo
const TOOLS_UDP_PORT = Number(process.env.TOOLS_UDP_PORT || 9870);
// live-restyle server (e.g. via an ssh tunnel to the GPU). When set, the lap monitor shows its
// AI-restyled frames and every finished frame is announced on our bus as {type:"frame"}.
const RESTYLE_URL = (process.env.RESTYLE_URL || '').replace(/\/$/, '');

const schema = JSON.parse(fs.readFileSync(path.join(root, 'schema/events.schema.json'), 'utf8'));
const validate = new Ajv({ allErrors: false, strict: false }).compile(schema);

// ---------------------------------------------------------------- state
const BASELINE_VITALS = { hr: 78, bp: '122/76', spo2: 99, rr: 12, etco2: 36, temp: 36.8 };
const state = {
  bleed: { intensity: 0, origin: { x: 0.62, y: 0.38 }, pulsatile: true },
  bile: { intensity: 0, origin: { x: 0.5, y: 0.5 } },
  smoke: { intensity: 0, until: 0 },
  warning: null,
  vitals: { ...BASELINE_VITALS },
  source: { mode: DEFAULT_SOURCE, ...(RESTYLE_URL ? { url: './api/restyled.jpg', pollMs: 20000 } : {}) },
};

function applyToState(ev) {
  switch (ev.type) {
    case 'bleed':
      state.bleed = { ...state.bleed, ...ev };
      delete state.bleed.type; delete state.bleed.id; delete state.bleed.ts; delete state.bleed.from;
      break;
    case 'bile':
      state.bile = { intensity: ev.intensity, origin: ev.origin || state.bile.origin };
      break;
    case 'smoke': {
      const durationMs = ev.durationMs ?? 8000;
      state.smoke = { intensity: ev.intensity ?? 0.7, until: durationMs ? ev.ts + durationMs : 0 };
      break;
    }
    case 'warning':
      state.warning = { text: ev.text, level: ev.level || 'critical', until: ev.durationMs ? ev.ts + ev.durationMs : 0 };
      break;
    case 'clear': {
      const t = ev.target || 'all';
      if (t === 'all' || t === 'bleed') state.bleed.intensity = 0;
      if (t === 'all' || t === 'bile') state.bile.intensity = 0;
      if (t === 'all' || t === 'smoke') state.smoke = { intensity: 0, until: 0 };
      if (t === 'all' || t === 'warning') state.warning = null;
      break;
    }
    case 'vitals':
      for (const k of Object.keys(BASELINE_VITALS)) if (ev[k] !== undefined) state.vitals[k] = ev[k];
      break;
    case 'source':
      state.source = { mode: ev.mode, ...(ev.whep ? { whep: ev.whep } : {}), ...(ev.mode === 'frames' ? { url: ev.url ?? state.source.url, ws: ev.ws ?? state.source.ws, pollMs: ev.pollMs ?? state.source.pollMs } : {}) };
      break;
    case 'reset':
      state.bleed.intensity = 0;
      state.bile.intensity = 0;
      state.smoke = { intensity: 0, until: 0 };
      state.warning = null;
      state.vitals = { ...BASELINE_VITALS };
      break;
  }
}

// ---------------------------------------------------------------- tracker liveness
// Poses from role "hardware" (WebSocket) or UDP count as the box tracker; everyone gets a
// once-a-second {type:"tracker", live, hz, tools} status for the HUD / director panel.
const tracker = { count: 0, last: 0, tools: [], live: false };
setInterval(() => {
  const live = Date.now() - tracker.last < 1000;
  if (live || tracker.live) broadcast({ type: 'tracker', live, hz: tracker.count, tools: live ? tracker.tools : [], ts: Date.now() });
  tracker.live = live; tracker.count = 0;
}, 1000);

// ---------------------------------------------------------------- event bus
const clients = new Map(); // ws -> { role, name }
const recent = []; // last 100 events, for the panel log

function presence() {
  const roles = {};
  for (const c of clients.values()) roles[c.role] = (roles[c.role] || 0) + 1;
  return { type: 'presence', roles, total: clients.size };
}
function send(ws, msg) { if (ws.readyState === 1) ws.send(JSON.stringify(msg)); }
function broadcast(msg, filter = () => true) {
  for (const [ws, info] of clients) if (filter(info)) send(ws, msg);
}

/** Validate, stamp, apply and fan out one event. Returns {ok, event|error}. */
export function publish(raw, from = 'api') {
  if (!validate(raw)) {
    const e = validate.errors?.[0];
    return { ok: false, error: `invalid event: ${e?.instancePath || ''} ${e?.message || ''}`.trim() };
  }
  if (raw.type === 'hello') return { ok: false, error: 'hello is only valid on a websocket' };
  if (raw.type === 'tools') { // high-rate pose stream: no state, no log, displays only
    broadcast({ ...raw, from }, (c) => c.role === 'display' || c.role === 'observer');
    if (from === 'hardware' || from === 'tracker') { tracker.count++; tracker.last = Date.now(); tracker.tools = raw.tools.map((t) => t.port || t.id); }
    return { ok: true };
  }
  const ev = { ...raw, id: randomUUID(), ts: Date.now(), from };
  applyToState(ev);
  recent.push(ev); if (recent.length > 100) recent.shift();
  if (ev.type === 'actuator') {
    // Hardware hook: only hardware daemons (and the director log) need these.
    broadcast(ev, (c) => c.role === 'hardware' || c.role === 'director');
  } else {
    broadcast(ev);
  }
  console.log(`[event] ${from} -> ${JSON.stringify(raw)}`);
  return { ok: true, event: ev };
}

// ---------------------------------------------------------------- http app
const app = express();
app.disable('x-powered-by');

// WHEP/WHIP signalling proxy -> MediaMTX (must be before the JSON body parser).
app.use('/mediamtx', express.raw({ type: () => true, limit: '1mb' }), async (req, res) => {
  const target = MEDIAMTX_URL + req.url;
  try {
    const headers = {};
    for (const h of ['content-type', 'authorization', 'if-match', 'accept']) if (req.headers[h]) headers[h] = req.headers[h];
    const r = await fetch(target, {
      method: req.method,
      headers,
      body: ['GET', 'HEAD'].includes(req.method) || !req.body?.length ? undefined : req.body,
      redirect: 'manual',
    });
    res.status(r.status);
    for (const [k, v] of r.headers) {
      const key = k.toLowerCase();
      if (['content-length', 'content-encoding', 'transfer-encoding', 'connection'].includes(key)) continue;
      // MediaMTX answers with Location: /<path>/whep/<session>; keep it inside the proxy.
      if (key === 'location' && v.startsWith('/')) res.setHeader(k, '/mediamtx' + v);
      else res.setHeader(k, v);
    }
    res.send(Buffer.from(await r.arrayBuffer()));
  } catch (err) {
    res.status(502).json({ error: `MediaMTX not reachable at ${MEDIAMTX_URL}: ${err.cause?.code || err.message}` });
  }
});

app.use(express.json({ limit: '256kb' }));

app.get('/api/config', (_req, res) => {
  res.json({ cameraPath: CAMERA_PATH, whep: `/mediamtx/${CAMERA_PATH}/whep`, whip: `/mediamtx/${CAMERA_PATH}/whip`, defaultSource: DEFAULT_SOURCE });
});
// image proxy for the external frame stream (lets WebGL use cross-origin frames)
app.get('/api/frame-proxy', async (req, res) => {
  const url = String(req.query.url || '');
  if (!/^https?:\/\//.test(url)) return res.status(400).json({ error: 'http(s) url required' });
  try {
    const r = await fetch(url, { redirect: 'follow' });
    const type = r.headers.get('content-type') || '';
    if (!r.ok || !type.startsWith('image/')) return res.status(502).json({ error: `upstream ${r.status} ${type}` });
    const buf = Buffer.from(await r.arrayBuffer());
    if (buf.length > 15e6) return res.status(413).end();
    res.set({ 'content-type': type, 'cache-control': 'no-store', 'access-control-allow-origin': '*' }).send(buf);
  } catch (e) { res.status(502).json({ error: e.message }); }
});
app.get('/api/state', (_req, res) => res.json({ state, presence: presence(), recent: recent.slice(-30) }));
app.get('/api/schema', (_req, res) => res.json(schema));
app.post('/api/events', (req, res) => {
  const list = Array.isArray(req.body) ? req.body : [req.body];
  const results = list.map((ev) => publish(ev, req.get('x-sender') || 'api'));
  const failed = results.filter((r) => !r.ok);
  res.status(failed.length ? 400 : 200).json(Array.isArray(req.body) ? results : results[0]);
});

const dist = path.join(root, 'dist');
if (fs.existsSync(dist)) {
  app.use(express.static(dist, { extensions: ['html'] }));
} else {
  app.get('/', (_req, res) => res.type('text').send('No dist/ build. Run `npm run build`, or use `npm run dev` (Vite on :5173).'));
}

// ---------------------------------------------------------------- restyle relay
// Browsers (the Quest) can't reach the GPU, so the server proxies frames (/api/restyled.jpg)
// and turns the restyle service's /ws/notify into {type:"frame"} bus events.
if (RESTYLE_URL) {
  app.get('/api/restyled.jpg', async (_req, res) => {
    try {
      const r = await fetch(`${RESTYLE_URL}/frame/result.jpg`);
      if (!r.ok) return res.status(r.status).end();
      res.set({ 'content-type': r.headers.get('content-type') || 'image/jpeg', 'cache-control': 'no-store' }).send(Buffer.from(await r.arrayBuffer()));
    } catch (e) { res.status(502).json({ error: e.message }); }
  });
  const connectNotify = () => {
    const ws = new WebSocket(`${RESTYLE_URL.replace(/^http/, 'ws')}/ws/notify`);
    ws.onmessage = (m) => { let info = {}; try { info = JSON.parse(m.data); } catch { /* ping */ } broadcast({ type: 'frame', url: './api/restyled.jpg', seq: info.gen, ts: Date.now() }); };
    ws.onclose = () => setTimeout(connectNotify, 3000);
    ws.onerror = () => {};
  };
  connectNotify();
}

// ---------------------------------------------------------------- dashboard feeds
// /dashboard.html shows the AI pipeline: raw + restyled video, the director's trace
// (observations, tool calls, dispatch results) and live GPU telemetry.
if (RESTYLE_URL) {
  // passthrough for restyle stats, snapshots and MJPEG streams
  app.get(/^\/api\/restyle\/(stats|state|frame\/\w+\.jpg|stream\/\w+\.mjpg)$/, async (req, res) => {
    const ac = new AbortController();
    req.on('close', () => ac.abort());
    try {
      const r = await fetch(`${RESTYLE_URL}/${req.params[0]}`, { signal: ac.signal });
      res.status(r.status).set({ 'content-type': r.headers.get('content-type') || 'application/octet-stream', 'cache-control': 'no-store' });
      if (!r.body) return res.end();
      for await (const chunk of r.body) if (!res.write(chunk)) await new Promise((ok) => res.once('drain', ok));
      res.end();
    } catch (e) { if (!res.headersSent) res.status(502).json({ error: e.message }); else res.end(); }
  });
}

// Director trace: every JSONL record the director logs is POSTed here and fanned out.
const traces = [];
app.post('/api/trace', (req, res) => {
  const list = Array.isArray(req.body) ? req.body : [req.body];
  for (const rec of list) {
    traces.push(rec); if (traces.length > 300) traces.shift();
    broadcast({ type: 'trace', rec }, (c) => c.role === 'observer' || c.role === 'dashboard');
  }
  res.json({ ok: true });
});
app.get('/api/trace', (_req, res) => res.json(traces.slice(-120)));

// GPU telemetry: GPU_STATS_CMD runs a loop on the GPU host (e.g. via ssh) that prints
// nvidia-smi samples; see scripts/gpu-stats.sh. Blocks are separated by a "---" line.
const gpu = { last: null, history: [] };
if (process.env.GPU_STATS_CMD) {
  const start = () => {
    const p = spawn('sh', ['-c', process.env.GPU_STATS_CMD], { stdio: ['ignore', 'pipe', 'inherit'] });
    let buf = '', block = [];
    p.stdout.on('data', (d) => {
      buf += d; let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
        if (line !== '---') { block.push(line); continue; }
        const sample = parseGpuBlock(block); block = [];
        if (!sample) continue;
        gpu.last = sample; gpu.history.push(sample); if (gpu.history.length > 240) gpu.history.shift();
        broadcast({ type: 'gpu', sample }, (c) => c.role === 'observer' || c.role === 'dashboard');
      }
    });
    p.on('exit', () => setTimeout(start, 3000));
  };
  start();
}
function parseGpuBlock(lines) {
  // line 0: name, util %, mem util %, mem used MiB, mem total MiB, power W, power limit W, temp C, sm clock MHz
  // then "proc,<pid>,<name>,<MiB>,<cmdline>"
  const g = (lines[0] || '').split(',').map((x) => x.trim());
  if (g.length < 9 || Number.isNaN(Number(g[1]))) return null;
  const procs = lines.slice(1).filter((l) => l.startsWith('proc,')).map((l) => {
    const [, pid, name, mib, ...cmd] = l.split(',');
    return { pid: Number(pid), name: name.trim(), memMiB: Number(mib), cmd: cmd.join(',').trim() };
  });
  const sm = {};
  for (const l of lines) if (l.startsWith('pmon,')) { const [, pid, s, m] = l.split(','); sm[pid] = { sm: Number(s) || 0, mem: Number(m) || 0 }; }
  for (const p of procs) Object.assign(p, sm[p.pid] || { sm: 0, mem: 0 });
  return { ts: Date.now(), name: g[0], util: +g[1], memUtil: +g[2], memUsed: +g[3], memTotal: +g[4], power: +g[5], powerLimit: +g[6], temp: +g[7], smClock: +g[8], procs };
}
app.get('/api/gpu', (_req, res) => res.json(gpu));

// ---------------------------------------------------------------- server
function tlsOptions() {
  const cert = process.env.CERT_FILE, key = process.env.KEY_FILE;
  if (cert && key) return { cert: fs.readFileSync(cert), key: fs.readFileSync(key) };
  // Fall back to a cached self-signed cert (the Quest browser lets you click through it).
  const dir = path.join(root, '.certs');
  const c = path.join(dir, 'selfsigned.crt'), k = path.join(dir, 'selfsigned.key');
  if (!fs.existsSync(c)) throw new Error('No certificate: run `npm run cert` (or set CERT_FILE/KEY_FILE from mkcert).');
  return { cert: fs.readFileSync(c), key: fs.readFileSync(k) };
}

const server = USE_HTTPS ? https.createServer(tlsOptions(), app) : http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

wss.on('connection', (ws, req) => {
  clients.set(ws, { role: 'observer', name: req.socket.remoteAddress });
  send(ws, { type: 'state', state, ts: Date.now() });
  send(ws, { type: 'tracker', live: Date.now() - tracker.last < 1000, hz: 0, tools: tracker.tools, ts: Date.now() });
  ws.on('message', (data) => {
    let msg;
    try { msg = JSON.parse(data.toString()); } catch { return send(ws, { type: 'error', error: 'not JSON' }); }
    if (msg?.type === 'hello') {
      if (!validate(msg)) return send(ws, { type: 'error', error: 'bad hello' });
      clients.set(ws, { role: msg.role, name: msg.name || clients.get(ws).name });
      broadcast(presence());
      if (msg.role === 'director') send(ws, { type: 'history', events: recent.slice(-30) });
      return;
    }
    const r = publish(msg, clients.get(ws).role);
    if (!r.ok) send(ws, { type: 'error', error: r.error });
  });
  ws.on('close', () => { clients.delete(ws); broadcast(presence()); });
  const ping = setInterval(() => ws.readyState === 1 && ws.ping(), 20000);
  ws.on('close', () => clearInterval(ping));
});

// Tool poses over UDP (one JSON `tools` message per datagram) for tracker hardware
// that would rather not speak WebSocket, e.g. a Python/OpenCV process or an ESP32.
const udp = dgram.createSocket('udp4');
udp.on('message', (buf) => { try { const r = publish(JSON.parse(buf.toString()), 'tracker'); if (!r.ok) console.warn('[udp]', r.error); } catch { /* ignore junk */ } });
udp.on('error', (e) => console.warn('[udp]', e.message));
udp.bind(TOOLS_UDP_PORT, HOST);

server.listen(PORT, HOST, () => {
  console.log(`lap-sim-vr server on ${USE_HTTPS ? 'https' : 'http'}://${HOST}:${PORT}  (ws path /ws, tool poses udp/${TOOLS_UDP_PORT}, MediaMTX ${MEDIAMTX_URL}, camera path "${CAMERA_PATH}")`);
});
