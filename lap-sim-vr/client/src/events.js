// Tiny reconnecting WebSocket client for the event hub (server/index.js).
export function connectEvents({ role, name, onEvent, onStatus }) {
  const url = new URL('/ws', location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  let ws, retry = 500;
  const api = {
    send(ev) { if (ws?.readyState === 1) { ws.send(JSON.stringify(ev)); return true; } return false; },
    get connected() { return ws?.readyState === 1; },
  };
  function open() {
    ws = new WebSocket(url);
    ws.onopen = () => { retry = 500; ws.send(JSON.stringify({ type: 'hello', role, name })); onStatus?.(true); };
    ws.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (e) { console.error(e); } };
    ws.onclose = () => { onStatus?.(false); setTimeout(open, retry); retry = Math.min(retry * 2, 5000); };
    ws.onerror = () => ws.close();
  }
  open();
  return api;
}
