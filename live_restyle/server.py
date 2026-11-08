"""Live restyle service.

Input  : JPEG frames over WebSocket (`/ingest`, pushed by pi/sender.py) or a
         looping video file (`--source file:clip.mp4`) for testing.
Process: one engine thread restyles the newest frames (see engines/).
Output : MJPEG (`/stream/out.mjpg`), WebSocket JPEG (`/ws/stream?which=out`),
         snapshot (`/frame/out.jpg`), and optionally WHEP/WebRTC via MediaMTX.
Control: `POST /events`, `GET /state`, `WS /ws/control`, `POST /config/reload`.

Run: python -m live_restyle.server --config config/restyle.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import struct
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import uvicorn
import yaml
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from .events import EventState
from .frames import Frame, FrameHub, RateMeter, decode_jpeg, encode_jpeg

log = logging.getLogger("live_restyle")
STATIC = Path(__file__).parent / "static"


class Service:
    def __init__(self, cfg_path: str, engine_name: str | None, source: str | None):
        self.cfg_path = cfg_path
        self.cfg = self._load_cfg()
        self.engine_name = engine_name or self.cfg.get("engine", "passthrough")
        self.source = source
        self.raw = FrameHub(history=16)
        self.out = FrameHub(history=16)
        self.events = EventState(self.cfg.get("events", {}))
        self.in_rate, self.out_rate = RateMeter(), RateMeter()
        self.lat = {"engine_ms": 0.0, "glass_to_out_ms": 0.0, "net_ms": 0.0}
        self.seq = 0
        self.engine = None
        self.engine_error = None
        self.running = True
        self.started = time.time()
        self._present_lock = threading.Lock()
        self._shown_t_cap = 0.0
        self.gen_seq = 0
        self.last_result = None
        self.result_frame = None
        self.results = FrameHub(history=4)   # finished frames only (no crossfade intermediates)
        self._play: list = []
        self._play_tail = 0.0
        self._play_cond = threading.Condition()

    def _load_cfg(self) -> dict:
        with open(self.cfg_path) as f:
            return yaml.safe_load(f)

    def reload_cfg(self) -> dict:
        self.cfg = self._load_cfg()
        self.events.cfg = self.cfg.get("events", {})
        if self.engine is not None and hasattr(self.engine, "update_config"):
            self.engine.update_config(self.cfg)
        return self.cfg

    # ----------------------------------------------------------- ingest
    def push(self, img: np.ndarray, t_cap: float, jpeg: bytes | None = None) -> None:
        now = time.time()
        self.seq += 1
        f = Frame(self.seq, img, t_cap, now, jpeg)
        self.in_rate.tick(now)
        self.lat["net_ms"] = 0.9 * self.lat["net_ms"] + 0.1 * (now - t_cap) * 1000
        self.raw.publish(f)

    def file_source(self, path: str) -> None:
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        period = 1.0 / fps
        nxt = time.time()
        while self.running:
            ok, img = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            self.push(img, time.time())
            nxt += period
            time.sleep(max(0, nxt - time.time()))
            if time.time() - nxt > 1:
                nxt = time.time()

    # ----------------------------------------------------------- engine
    def engine_loop(self) -> None:
        from .engines import load_engine
        try:
            self.engine = load_engine(self.engine_name, self.cfg)
        except Exception as e:  # keep serving raw so problems are visible
            log.exception("engine load failed")
            self.engine_error = repr(e)
            return
        if hasattr(self.engine, "run"):   # engines that drive themselves (e.g. OpenAI workers)
            self.engine.run(self)
            return
        ver = 0
        while self.running:
            _, ver_now = self.raw.wait(ver, timeout=0.5)
            items, ver = self.raw.since(ver)
            if not items:
                continue
            # newest frames, at most max_batch, oldest first
            batch = items[-self.engine.max_batch:]
            if self.engine.max_batch == 1:
                batch = items[-1:]
            t0 = time.time()
            try:
                outs = self.engine.process(batch, self.events)
            except Exception as e:
                log.exception("engine error")
                self.engine_error = repr(e)
                time.sleep(0.5)
                continue
            dt = time.time() - t0
            self.lat["engine_ms"] = 0.8 * self.lat["engine_ms"] + 0.2 * dt * 1000
            self._emit(batch, outs, dt)

    def _emit(self, batch: list[Frame], outs: list[np.ndarray], dt: float) -> None:
        # Spread a batch's outputs over the engine time so motion stays smooth
        # instead of arriving in bursts; a playout thread releases them.
        n = len(batch)
        step = dt / n if n > 1 else 0.0
        now = time.time()
        with self._play_cond:
            base = max(now, self._play_tail)
            for i, (f, img) in enumerate(zip(batch, outs)):
                self._play.append((base + i * step, f, img))
            self._play_tail = base + n * step
            self._play_cond.notify()

    def playout_loop(self) -> None:
        while self.running:
            with self._play_cond:
                while not self._play:
                    self._play_cond.wait(0.5)
                due, f, img = self._play.pop(0)
            wait = due - time.time()
            if wait > 0:
                time.sleep(wait)
            now = time.time()
            o = Frame(f.seq, img, f.t_cap, f.t_recv, meta={"t_out": now})
            self.out_rate.tick(now)
            self.lat["glass_to_out_ms"] = 0.8 * self.lat["glass_to_out_ms"] + 0.2 * (now - f.t_cap) * 1000
            self.out.publish(o)

    def present(self, f: Frame, img: np.ndarray, meta: dict | None = None) -> None:
        """Publish a slow-engine result: hold the last good frame, crossfade to the new one."""
        with self._present_lock:
            if f.t_cap < self._shown_t_cap:
                return  # an older request finished after a newer one; drop it
            self._shown_t_cap = f.t_cap
            self.gen_seq += 1
            gen = self.gen_seq
            prev, _ = self.out.latest()
        o = self.cfg.get("output", {})
        W, H = int(o.get("width", 1024)), int(o.get("height", 576))
        if img.shape[:2] != (H, W):
            img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
        now = time.time()
        self.out_rate.tick(now)
        self.lat["glass_to_out_ms"] = (now - f.t_cap) * 1000
        info = {"gen": gen, "seq": f.seq, "t_cap": f.t_cap, "t_out": now, **(meta or {})}
        self.last_result = info
        self.result_frame = Frame(f.seq, img, f.t_cap, f.t_recv, meta={"t_out": now, **info})
        self.results.publish(self.result_frame)
        xf = float(o.get("crossfade_s", 1.5))
        fps = float(o.get("crossfade_fps", 15))
        if prev is None or xf <= 0 or prev.img.shape != img.shape:
            self.out.publish(Frame(f.seq, img, f.t_cap, f.t_recv, meta={"t_out": now, **info}))
            return
        a_img = prev.img.astype(np.float32)
        b_img = img.astype(np.float32)
        n = max(1, int(xf * fps))
        for i in range(1, n + 1):
            if self.gen_seq != gen:
                return  # a newer result took over
            a = i / n
            a = a * a * (3 - 2 * a)  # smoothstep
            mix = (a_img * (1 - a) + b_img * a).astype(np.uint8) if i < n else img
            self.out.publish(Frame(f.seq, mix, f.t_cap, f.t_recv, meta={"t_out": time.time(), **info}))
            time.sleep(1 / fps)

    def stats(self) -> dict:
        return {
            "engine": self.engine_name,
            "engine_ready": self.engine is not None,
            "engine_error": self.engine_error,
            "in_fps": round(self.in_rate.fps, 1),
            "out_fps": round(self.out_rate.fps, 1),
            **{k: round(v, 1) for k, v in self.lat.items()},
            "events": self.events.snapshot()["events"],
            "engine_stats": getattr(self.engine, "stats", None),
            "last_result": self.last_result,
            "uptime_s": round(time.time() - self.started),
        }


# ------------------------------------------------------------------- app

def build_app(svc: Service) -> FastAPI:
    app = FastAPI(title="live-restyle")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    jpeg_q = int(svc.cfg.get("output", {}).get("jpeg_quality", 85))

    def hub(which: str) -> FrameHub:
        return {"raw": svc.raw, "result": svc.results}.get(which, svc.out)

    def frame_jpeg(f: Frame, which: str) -> bytes:
        if which == "raw" and f.jpeg is not None:
            return f.jpeg
        cache = f.meta.setdefault("_jpeg", {})
        if jpeg_q not in cache:
            cache[jpeg_q] = encode_jpeg(f.img, jpeg_q)
        return cache[jpeg_q]

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "viewer.html").read_text()

    @app.get("/stats")
    def stats():
        return svc.stats()

    @app.get("/state")
    def state():
        return {**svc.events.snapshot(), "prompt": svc.cfg.get("prompt", {})}

    @app.post("/events")
    async def post_event(req: Request):
        try:
            msg = await req.json()
            msgs = msg if isinstance(msg, list) else [msg]
            res = None
            for m in msgs:
                res = svc.events.apply(m)
            return res
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/config/reload")
    def reload_cfg():
        svc.reload_cfg()
        return {"ok": True}

    @app.get("/frame/{which}.jpg")
    def snapshot(which: str):
        f, _ = hub(which).latest()
        if f is None:
            return Response(status_code=503)
        return Response(frame_jpeg(f, which), media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.get("/stream/{which}.mjpg")
    async def mjpeg(which: str):
        h = hub(which)

        async def gen():
            ver = 0
            while True:
                f, ver = await asyncio.to_thread(h.wait, ver, 2.0)
                if f is None:
                    continue
                data = frame_jpeg(f, which)
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")

        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                                 headers={"Cache-Control": "no-store"})

    @app.websocket("/ws/stream")
    async def ws_stream(ws: WebSocket):
        """Binary messages: uint32 header length (LE) + JSON header + JPEG."""
        await ws.accept()
        which = ws.query_params.get("which", "out")
        h = hub(which)
        ver = 0
        try:
            while True:
                f, ver = await asyncio.to_thread(h.wait, ver, 2.0)
                if f is None:
                    continue
                hdr = json.dumps({"seq": f.seq, "t_cap": f.t_cap, "t_recv": f.t_recv,
                                  "t_send": time.time(),
                                  **{k: v for k, v in f.meta.items() if not k.startswith("_")}}).encode()
                await ws.send_bytes(struct.pack("<I", len(hdr)) + hdr + frame_jpeg(f, which))
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.websocket("/ws/notify")
    async def ws_notify(ws: WebSocket):
        """JSON message for every finished frame (not crossfade steps):
        {"gen", "seq", "t_cap", "t_out", "gen_s", "cost_usd", "url": "/frame/result.jpg?gen=N"}"""
        await ws.accept()
        ver = 0
        try:
            while True:
                f, ver = await asyncio.to_thread(svc.results.wait, ver, 5.0)
                if f is None:
                    continue
                info = {k: v for k, v in f.meta.items() if not k.startswith("_")}
                await ws.send_json({**info, "url": f"/frame/result.jpg?gen={info.get('gen')}"})
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.websocket("/ingest")
    async def ingest(ws: WebSocket):
        """Binary messages: float64 capture timestamp (LE, unix s) + JPEG."""
        await ws.accept()
        log.info("ingest client connected: %s", ws.client)
        try:
            while True:
                data = await ws.receive_bytes()
                t_cap = struct.unpack("<d", data[:8])[0]
                jpeg = data[8:]
                img = await asyncio.to_thread(decode_jpeg, jpeg)
                svc.push(img, t_cap, jpeg)
        except (WebSocketDisconnect, RuntimeError):
            log.info("ingest client disconnected")

    @app.websocket("/ws/control")
    async def ws_control(ws: WebSocket):
        """Send event JSON; receives the resulting state. Also pushes stats every second."""
        await ws.accept()

        async def pusher():
            while True:
                await asyncio.sleep(1.0)
                await ws.send_json({"stats": svc.stats()})

        task = asyncio.create_task(pusher())
        try:
            while True:
                msg = await ws.receive_json()
                try:
                    await ws.send_json(svc.events.apply(msg))
                except ValueError as e:
                    await ws.send_json({"error": str(e)})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            task.cancel()

    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/restyle.yaml")
    ap.add_argument("--engine", default=None, help="override engine in config")
    ap.add_argument("--source", default=None, help="file:<video> to loop a clip instead of /ingest")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--rtsp", default=None, help="also publish output to this RTSP URL (MediaMTX → WHEP)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    svc = Service(args.config, args.engine, args.source)
    threading.Thread(target=svc.engine_loop, daemon=True, name="engine").start()
    threading.Thread(target=svc.playout_loop, daemon=True, name="playout").start()
    if args.source and args.source.startswith("file:"):
        threading.Thread(target=svc.file_source, args=(args.source[5:],), daemon=True, name="file").start()
    rtsp = args.rtsp or svc.cfg.get("output", {}).get("rtsp")
    if rtsp:
        from .publish import RtspPublisher
        RtspPublisher(svc.out, rtsp, svc.cfg.get("output", {})).start()
    uvicorn.run(build_app(svc), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
