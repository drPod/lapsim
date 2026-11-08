"""Event outputs: the VR app (WebSocket, role "agent"), live-restyle's control endpoint, and the log."""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import websockets

log = logging.getLogger("sinks")

VR_TYPES = {"bleed", "bile", "smoke", "warning", "clear", "vitals", "say", "reset"}


class VRSink:
    """Persistent WebSocket to the VR relay; falls back to POST /api/events when the socket is down."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ws = None
        self.http = httpx.AsyncClient(timeout=5)
        self._task = None

    async def start(self):
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        backoff = 0.5
        while True:
            try:
                async with websockets.connect(self.cfg["ws_url"], open_timeout=10, ping_interval=20) as ws:
                    await ws.send(json.dumps({"type": "hello", "role": "agent", "name": self.cfg.get("name", "director")}))
                    self.ws, backoff = ws, 0.5
                    log.info("VR websocket connected")
                    async for _ in ws:  # drain broadcasts; we don't act on them
                        pass
            except Exception as e:  # noqa: BLE001
                log.warning("VR websocket: %s", e)
            self.ws = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5)

    async def send(self, ev: dict) -> str:
        if ev["type"] not in VR_TYPES:
            return "skip"
        if self.ws is not None:
            try:
                await self.ws.send(json.dumps(ev))
                return "ws"
            except Exception:  # noqa: BLE001
                self.ws = None
        try:
            r = await self.http.post(self.cfg["http_url"], json=ev)
            return f"http {r.status_code}"
        except Exception as e:  # noqa: BLE001
            return f"fail {e.__class__.__name__}"


class RestyleSink:
    """live-restyle's POST /events (bleed / smoke / bile_leak, severity 0 stops one, type clear wipes all)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.http = httpx.AsyncClient(base_url=cfg["url"], timeout=3)

    @staticmethod
    def translate(ev: dict) -> dict | None:
        t = ev["type"]
        body = None
        if t in ("bleed", "bile"):
            body = {"type": "bleed" if t == "bleed" else "bile_leak", "severity": ev["intensity"]}
        elif t == "smoke":
            body = {"type": "smoke", "severity": ev.get("intensity", 0.6)}
            if ev.get("durationMs"):
                body["duration"] = ev["durationMs"] / 1000
        elif t == "clear" and ev.get("target") == "smoke":
            body = {"type": "smoke", "severity": 0}
        elif t == "clear" and ev.get("target", "all") == "all":
            body = {"type": "clear"}
        if body and ev.get("origin"):
            body.update(x=ev["origin"]["x"], y=ev["origin"]["y"])
        return body

    async def send(self, ev: dict) -> str:
        body = self.translate(ev)
        if body is None:
            return "skip"
        try:
            r = await self.http.post(self.cfg.get("path", "/events"), json=body)
            return f"http {r.status_code}"
        except Exception as e:  # noqa: BLE001
            return f"fail {e.__class__.__name__}"


class JsonlLog:
    def __init__(self, path):
        self.f = open(path, "a", buffering=1)

    def write(self, kind: str, **data):
        rec = {"ts": round(time.time(), 3), "iso": time.strftime("%H:%M:%S"), "kind": kind, **data}
        self.f.write(json.dumps(rec, default=str) + "\n")
        return rec
