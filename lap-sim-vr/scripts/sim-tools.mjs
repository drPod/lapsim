#!/usr/bin/env node
// Stand-in for the physical instrument tracker: streams `tools` pose messages to the
// lap-sim-vr server exactly as the tracker will (WebSocket or UDP, 60 Hz).
//
//   node scripts/sim-tools.mjs                       # scripted procedure over ws://localhost:8787/ws
//   node scripts/sim-tools.mjs --mode sweep          # slow sinusoidal motion of all tools
//   node scripts/sim-tools.mjs --udp 127.0.0.1:9870  # send over UDP instead
//   node scripts/sim-tools.mjs --url wss://host/ws --loop
//
// Message format (see schema/events.schema.json "tools"):
//   {"type":"tools","t":<ms>,"tools":[{"id":"right","port":"epigastric","tool":"maryland",
//     "yaw":0.05,"pitch":-0.1,"roll":0,"depth":0.11,"jaw":0.8,"energy":false}, ...]}
import dgram from 'node:dgram';
import fs from 'node:fs';
import WebSocket from 'ws';
import { Autopilot } from '../client/src/abdomen/autopilot.js';
import { DEFAULT_TOOLS } from '../client/src/abdomen/tools.js';

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] ?? true : d; };
const mode = arg('mode', 'procedure'), hz = Number(arg('hz', 60)), loop = process.argv.includes('--loop');
const landmarks = JSON.parse(fs.readFileSync(new URL('../client/src/abdomen/landmarks.json', import.meta.url)));

let send;
if (arg('udp')) {
  const [host, port] = String(arg('udp')).split(':'); const sock = dgram.createSocket('udp4');
  send = (msg) => sock.send(JSON.stringify(msg), Number(port || 9870), host || '127.0.0.1');
  console.log(`streaming ${mode} to udp ${host}:${port || 9870} at ${hz} Hz`);
} else {
  const url = arg('url', 'ws://localhost:8787/ws');
  const ws = new WebSocket(url, { rejectUnauthorized: false });
  await new Promise((res, rej) => { ws.once('open', res); ws.once('error', rej); });
  ws.send(JSON.stringify({ type: 'hello', role: 'hardware', name: 'sim-tools' }));
  send = (msg) => ws.readyState === 1 && ws.send(JSON.stringify(msg));
  console.log(`streaming ${mode} to ${url} at ${hz} Hz`);
}

let ap = new Autopilot(null, { landmarks });
let t = 0; const dt = 1 / hz;
setInterval(() => {
  t += dt;
  let tools;
  if (mode === 'sweep') {
    tools = Object.entries(DEFAULT_TOOLS).filter(([id]) => id !== 'scope').map(([id, p], k) => ({
      id, ...p, yaw: 0.12 * Math.sin(t * 0.7 + k), pitch: 0.1 * Math.sin(t * 0.5 + k * 2), depth: 0.08 + 0.025 * Math.sin(t * 0.4 + k), jaw: 0.5 + 0.5 * Math.sin(t * 1.3 + k),
    }));
  } else {
    tools = ap.tick(dt);
    if (ap.t > ap.duration) { if (!loop) { console.log('procedure finished'); process.exit(0); } ap = new Autopilot(null, { landmarks }); }
  }
  send({ type: 'tools', t: Date.now(), tools: tools.map(({ id, port, tool, yaw, pitch, roll, depth, jaw, energy }) => ({ id, port, tool, yaw: +yaw.toFixed(4), pitch: +pitch.toFixed(4), roll: +(roll || 0).toFixed(3), depth: +depth.toFixed(4), jaw: +jaw.toFixed(3), energy: !!energy })) });
}, 1000 / hz);
