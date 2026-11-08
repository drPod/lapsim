"""Send tool poses to the simulator: WebSocket (remote preview), UDP or HTTP (LAN)."""
import asyncio
import collections
import json
import logging
import socket
import time

import aiohttp

log = logging.getLogger("sender")


class RateMeter:
    def __init__(self, window=2.0):
        self.window = window
        self.ts = collections.deque()

    def tick(self, t=None):
        t = time.monotonic() if t is None else t
        self.ts.append(t)
        while self.ts and self.ts[0] < t - self.window:
            self.ts.popleft()

    def rate(self):
        t = time.monotonic()
        while self.ts and self.ts[0] < t - self.window:
            self.ts.popleft()
        return len(self.ts) / self.window


class Sender:
    def __init__(self, cfg):
        self.cfg = cfg
        self.latest = None          # (poses, capture_ts)
        self.event = asyncio.Event()
        self.ws = None
        self.connected = False
        self.last_error = None
        self.rate = RateMeter()
        self.latency_ms = None      # capture -> handed to the socket
        self.enabled = True
        self.udp = None
        self.presence = None

    def push(self, poses, ts):
        self.latest = (poses, ts)
        self.event.set()

    def message(self, poses):
        return json.dumps({"type": "tools", "tools": poses}, separators=(",", ":"))

    async def run(self):
        sc = self.cfg["sender"]
        tasks = [asyncio.create_task(self._pump())]
        if sc.get("ws_url"):
            tasks.append(asyncio.create_task(self._ws_loop(sc)))
        if sc.get("udp"):
            host, port = sc["udp"].rsplit(":", 1)
            self.udp = (socket.socket(socket.AF_INET, socket.SOCK_DGRAM), (host, int(port)))
        await asyncio.gather(*tasks)

    async def _ws_loop(self, sc):
        backoff = 0.5
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(sc["ws_url"], heartbeat=15, timeout=10) as ws:
                        await ws.send_str(json.dumps(
                            {"type": "hello", "role": sc.get("role", "hardware"), "name": sc.get("name", "box-tracker")}))
                        self.ws, self.connected, backoff = ws, True, 0.5
                        self.last_error = None
                        log.info("connected to %s", sc["ws_url"])
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    d = json.loads(msg.data)
                                except ValueError:
                                    continue
                                if d.get("type") == "presence":
                                    self.presence = d.get("roles")
                                elif d.get("type") == "error":
                                    self.last_error = d.get("error")
                                    log.warning("sim error: %s", d.get("error"))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:  # network errors: keep retrying
                self.last_error = f"{type(e).__name__}: {e}"
            self.ws, self.connected = None, False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)

    async def _pump(self):
        sc = self.cfg["sender"]
        min_dt = 1.0 / max(1, sc.get("max_rate", 60))
        http = aiohttp.ClientSession() if sc.get("http") else None
        last = 0.0
        while True:
            await self.event.wait()
            self.event.clear()
            wait = last + min_dt - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            if not self.latest or not self.enabled:
                continue
            poses, ts = self.latest
            if not poses:
                continue
            msg = self.message(poses)
            sent = False
            if self.ws is not None and not self.ws.closed:
                try:
                    await self.ws.send_str(msg)
                    sent = True
                except Exception as e:
                    self.last_error = str(e)
            if self.udp:
                try:
                    self.udp[0].sendto(msg.encode(), self.udp[1])
                    sent = True
                except OSError as e:
                    self.last_error = str(e)
            if http is not None:
                try:
                    async with http.post(sc["http"], data=msg,
                                         headers={"content-type": "application/json"}) as r:
                        sent = r.status < 300
                except Exception as e:
                    self.last_error = str(e)
            if sent:
                last = time.monotonic()
                self.rate.tick(last)
                self.latency_ms = (last - ts) * 1000
