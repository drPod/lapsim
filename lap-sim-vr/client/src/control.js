import { connectEvents } from './events.js';

const $ = (id) => document.getElementById(id);
const log = $('log');
function line(text, cls = '') {
  const d = document.createElement('div'); d.textContent = text; if (cls) d.className = cls;
  log.prepend(d); while (log.childElementCount > 200) log.lastChild.remove();
}
const fmt = (ev) => { const { id, ts, from, ...rest } = ev; return `${new Date(ts || Date.now()).toLocaleTimeString()}  ${from || ''}  ${JSON.stringify(rest)}`; };

const bus = connectEvents({
  role: 'director', name: 'control-panel',
  onStatus: (ok) => { const c = $('conn'); c.className = `pill ${ok ? 'ok' : 'bad'}`; c.textContent = ok ? 'connected' : 'reconnecting…'; },
  onEvent: (ev) => {
    if (ev.type === 'tracker') { const t = $('tracker'); t.className = `pill ${ev.live ? 'ok' : 'bad'}`; t.textContent = ev.live ? `TRACKER LIVE · ${ev.hz} Hz · ${ev.tools.join(', ')}` : 'NO TRACKER'; return; }
    if (ev.type === 'presence') {
      const r = ev.roles; $('presence').textContent = `headsets/displays: ${r.display || 0} · directors: ${r.director || 0} · agents: ${r.agent || 0} · hardware: ${r.hardware || 0}`;
    } else if (ev.type === 'history') ev.events.forEach((e) => line(fmt(e)));
    else if (ev.type === 'error') line(`error: ${ev.error}`, 'err');
    else if (ev.type !== 'state') line(fmt(ev));
  },
});
const send = (ev) => { if (!bus.send(ev)) line('not connected', 'err'); };

document.querySelectorAll('[data-ev]').forEach((b) => b.addEventListener('click', () => send(JSON.parse(b.dataset.ev))));
$('bleedGo').onclick = () => send({ type: 'bleed', intensity: +$('bleedI').value, pulsatile: $('bleedP').checked, origin: { x: +$('bleedX').value, y: +$('bleedY').value } });
$('warnGo').onclick = () => send({ type: 'warning', text: $('warnText').value, level: $('warnLevel').value, durationMs: Math.round(+$('warnDur').value * 1000) });
$('vGo').onclick = () => send({ type: 'vitals', hr: +$('vHr').value, bp: $('vBp').value, spo2: +$('vSpo2').value, etco2: +$('vEtco2').value, rr: +$('vRr').value, transitionMs: Math.round(+$('vT').value * 1000) });
$('sayGo').onclick = () => send({ type: 'say', speaker: $('sayWho').value, text: $('sayText').value });
$('frUrl').value = localStorage.getItem('frUrl') || '';
$('frWs').value = localStorage.getItem('frWs') || '';
$('frGo').onclick = () => { localStorage.setItem('frUrl', $('frUrl').value); localStorage.setItem('frWs', $('frWs').value); send({ type: 'source', mode: 'frames', url: $('frUrl').value, ws: $('frWs').value, pollMs: Math.round(+$('frPoll').value * 1000) }); };
$('frNext').onclick = () => send({ type: 'frame' });
$('rawGo').onclick = () => { try { send(JSON.parse($('raw').value)); } catch (e) { line(`bad JSON: ${e.message}`, 'err'); } };
