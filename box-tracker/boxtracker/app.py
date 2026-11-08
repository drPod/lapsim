"""The running tracker: capture thread, calibration wizard, debug web server, sender."""
import asyncio
import json
import logging
import math
import os
import threading
import time

import cv2
import numpy as np
from aiohttp import web

from . import calib, config as config_mod
from .keys import KeyReader
from .sender import RateMeter, Sender
from .tracker import Tracker, colour_mask

log = logging.getLogger("app")
STATIC = os.path.join(os.path.dirname(__file__), "static")

STEPS = ["focus", "intrinsics", "lid", "colours", "wiggle", "jaw", "done"]


class Wizard:
    """Guided calibration. Driven by the web page buttons/clicks or the terminal."""

    def __init__(self, app):
        self.app = app
        self.active = False
        self.step = None
        self.msg = ""
        self.clicks = {}
        self.colour_clicks = []
        self.collector = None
        self.lines = {}
        self.jaw_samples = None
        self.result = {}

    # --- helpers -----------------------------------------------------------------------
    @property
    def cfg(self):
        return self.app.cfg

    def prompt(self):
        cfg = self.cfg
        s = self.step
        if s == "focus":
            return ("Step 1/6 Focus: check the whole lid is in the picture (U, R, L and A marks "
                    "visible) and nothing moves. Then press Focus.")
        if s == "intrinsics":
            n = len(self.collector.views) if self.collector else 0
            if self.collector:
                return (f"Step 2/6 Lens: show the checkerboard (open /checker on a phone, full "
                        f"brightness) to the camera at about lid height, and slowly tilt and move it "
                        f"around the whole picture. Views: {n}/{self.collector.target}")
            return ("Step 2/6 Lens (optional): press Start and show the checkerboard page on a phone, "
                    "or press Skip to use the built-in lens values.")
        if s == "lid":
            order = cfg["lid"]["click_order"]
            nxt = next((k for k in order if k not in self.clicks), None)
            if nxt:
                what = "the black U dot" if nxt == "U" else f"the centre of hole {nxt}"
                return f"Step 3/6 Lid: click {what} on the picture. ({len(self.clicks)}/4)"
            return f"Step 3/6 Lid: done, fit error {self.result.get('lid_rms', 0):.1f} px. Press Next (or Undo)."
        if s == "colours":
            i = len(self.colour_clicks)
            tools = cfg["tools"]
            if i < 2 * len(tools):
                t = tools[i // 2]
                band = "band 1 (upper)" if i % 2 == 0 else "band 2 (lower, nearer the lid)"
                return (f"Step 4/6 Colours: put all sticks in their holes, standing up. Click "
                        f"{band} of the {t['colour'].upper()} stick (hole {t['hole']}).")
            return "Step 4/6 Colours: done. Press Next."
        if s == "wiggle":
            parts = []
            for t in cfg["tools"]:
                L = self.lines.get(t["id"], [])
                parts.append(f"{t['colour']} {min(100, int(100 * self._wiggle_progress(L)))}%")
            return ("Step 5/6 Wiggle: slowly circle each stick around its hole (keep fingers off the "
                    "bands) until all reach 100%. " + ", ".join(parts) + ". Press Next when done "
                    "(or to skip).")
        if s == "jaw":
            done = [t["colour"] for t in cfg["tools"]
                    if t.get("jaw_open_score") is not None and t.get("jaw_closed_score") is not None]
            return ("Step 6/6 Jaw: hold a stick like a syringe. Thumb OFF the top: press Open for "
                    "that stick. Thumb ON the top: press Closed. Calibrated: "
                    + (", ".join(done) or "none") + ". Press Finish when done.")
        if s == "done":
            return "Calibration saved. Tracking and streaming to the simulator."
        return ""

    def start(self, step="focus"):
        self.active = True
        self.app.sender.enabled = False
        self.step = step
        self.clicks, self.colour_clicks, self.lines = {}, [], {}
        self.collector = None
        self.msg = ""
        if step == "focus":
            self.app.source.unlock()

    def cancel(self):
        self.active = False
        self.step = None
        self.collector = None
        self.app.sender.enabled = True
        self.app.reload(config_mod.load(self.app.config_path))

    def goto(self, step):
        self.step = step
        if step == "done":
            self.finish()

    def finish(self):
        config_mod.save(self.cfg, self.app.config_path)
        self.app.reload(self.cfg)
        self.active = False
        self.app.sender.enabled = True
        self.step = "done"

    # --- commands ----------------------------------------------------------------------
    def command(self, d):
        cmd = d.get("cmd")
        cfg = self.cfg
        if cmd != "click":
            self.msg = ""
        if cmd == "focus":
            s = self.app.source.autofocus_and_lock()
            cfg["camera"].update({k: s[k] for k in ("lens_position", "exposure_us", "gain", "colour_gains")})
            self.msg = (f"Focus locked at {1 / max(s['lens_position'], 1e-3) * 100:.0f} cm, exposure "
                        f"{s['exposure_us'] / 1000:.1f} ms, gain {s['gain']:.1f}"
                        + ("" if s.get("af_ok") else " (autofocus did not confirm; check sharpness)"))
            config_mod.save(cfg, self.app.config_path)
            self.goto("intrinsics")
        elif cmd == "intrinsics_start":
            c = cfg["camera"]
            self.collector = calib.IntrinsicsCollector((c["width"], c["height"]))
        elif cmd == "intrinsics_skip":
            c = cfg["camera"]
            if not cfg["intrinsics"].get("calibrated"):
                cfg["intrinsics"]["K"] = config_mod.nominal_K(c["width"], c["height"], c["sensor_size"][0])
                cfg["intrinsics"]["dist"] = [0, 0, 0, 0, 0]
            self.collector = None
            self.app.reload(cfg)
            self.goto("lid")
        elif cmd == "click":
            self.click(float(d["x"]), float(d["y"]))
        elif cmd == "undo":
            if self.step == "lid" and self.clicks:
                self.clicks.pop(list(self.clicks)[-1])
            elif self.step == "colours" and self.colour_clicks:
                self.colour_clicks.pop()
        elif cmd == "next":
            if self.step == "lid":
                if len(self.clicks) < 4:
                    self.msg = "Click all 4 marks first."
                    return
                self.goto("colours")
            elif self.step == "colours":
                self.apply_colours()
                self.goto("wiggle")
            elif self.step == "wiggle":
                self.apply_wiggle()
                self.goto("jaw")
            elif self.step == "jaw":
                self.goto("done")
            elif self.step == "focus":
                self.goto("intrinsics")
            elif self.step == "intrinsics":
                self.command({"cmd": "intrinsics_skip"})
        elif cmd == "jaw":
            self.jaw_samples = {"tool": d["tool"], "state": d["state"], "until": time.monotonic() + 1.0,
                                "vals": []}
        elif cmd == "goto":
            self.goto(d["step"])

    def click(self, x, y):
        cfg = self.cfg
        if self.step == "lid":
            order = cfg["lid"]["click_order"]
            nxt = next((k for k in order if k not in self.clicks), None)
            if nxt is None:
                return
            self.clicks[nxt] = [x, y]
            if len(self.clicks) == 4:
                try:
                    rvec, tvec, rms = calib.solve_lid(cfg, self.clicks)
                except ValueError as e:
                    self.msg = str(e)
                    return
                cfg["lid"]["rvec"], cfg["lid"]["tvec"] = rvec.tolist(), tvec.tolist()
                cfg["lid"]["reproj_px"] = rms
                cfg["lid"]["clicks"] = self.clicks
                cam = calib.camera_position(rvec, tvec)
                self.result["lid_rms"] = rms
                self.msg = (f"Camera is {cam[2] * 100:.0f} cm above the lid, "
                            f"{math.hypot(cam[0], cam[1]) * 100:.0f} cm off-centre.")
                if rms > 8:
                    self.msg += " The fit is poor: check the click order (U, R, L, A) or the hole positions."
                self.app.reload(cfg)
        elif self.step == "colours":
            if len(self.colour_clicks) < 2 * len(cfg["tools"]):
                self.colour_clicks.append([x, y])
                if len(self.colour_clicks) % 2 == 0:
                    self.apply_colours()

    def apply_colours(self):
        cfg = self.cfg
        hsv = self.app.latest_hsv
        if hsv is None:
            return
        for i, t in enumerate(cfg["tools"]):
            pts = self.colour_clicks[2 * i:2 * i + 2]
            if len(pts) < 2:
                continue
            spec = calib.sample_colour(hsv, pts)
            t["hsv"] = spec
            areas = [calib.blob_area_at(hsv, spec, p) for p in pts]
            t["band_area"] = int(min(areas)) if min(areas) > 0 else None
        self.app.reload(cfg)

    @staticmethod
    def _wiggle_progress(lines):
        if len(lines) < 10:
            return len(lines) / 60
        ang = np.array([l[2] for l in lines])
        spread = np.ptp(np.unwrap(2 * ang) / 2)
        return min(len(lines) / 60, spread / math.radians(30))

    def feed_wiggle(self):
        for tt in self.app.tracker.tools:
            if tt.status == "tracking" and "b2" in tt.dbg:
                p2, p1 = np.array(tt.dbg["b2"]), np.array(tt.dbg["b1"])
                d = p1 - p2
                if np.linalg.norm(d) < 8:
                    continue
                L = self.lines.setdefault(tt.tool["id"], [])
                if L and np.linalg.norm(np.array(L[-1][0]) - p2) < 2:
                    continue
                L.append((p2.tolist(), p1.tolist(), math.atan2(d[1], d[0])))

    def apply_wiggle(self):
        """Intersect each stick's image lines to find its pivot, then refit the lid."""
        cfg = self.cfg
        refined = {}
        for t in cfg["tools"]:
            L = self.lines.get(t["id"], [])
            if self._wiggle_progress(L) < 0.6:
                continue
            A = np.zeros((2, 2))
            b = np.zeros(2)
            for p2, p1, _ in L:
                p2, p1 = np.array(p2), np.array(p1)
                d = (p1 - p2) / np.linalg.norm(p1 - p2)
                n = np.array([-d[1], d[0]])
                A += np.outer(n, n)
                b += n * (n @ p2)
            refined[t["hole"]] = np.linalg.solve(A, b).tolist()
        if not refined:
            self.msg = "Not enough wiggle data; kept the clicked hole positions."
            return
        clicks = dict(cfg["lid"].get("clicks") or self.clicks)
        moved = {k: math.dist(v, clicks[k]) for k, v in refined.items() if k in clicks}
        # Pivot projections sit at pivot_z, slightly below the clicked surface marks.
        pz = cfg["lid"]["pivot_z"]
        names = list(clicks)
        obj = np.array([[*cfg["lid"]["points"][k], pz if k in refined else 0.0] for k in names])
        img = np.array([refined.get(k, clicks[k]) for k in names], float)
        K = np.asarray(cfg["intrinsics"]["K"], float)
        dist = np.asarray(cfg["intrinsics"]["dist"], float)
        ok, rv, tv = cv2.solvePnP(obj, img, K, dist, np.asarray(cfg["lid"]["rvec"], float).reshape(3, 1),
                                  np.asarray(cfg["lid"]["tvec"], float).reshape(3, 1),
                                  useExtrinsicGuess=True, flags=cv2.SOLVEPNP_ITERATIVE)
        proj = cv2.projectPoints(obj, rv, tv, K, dist)[0].reshape(-1, 2)
        rms = float(np.sqrt(((proj - img) ** 2).sum(1).mean()))
        cfg["lid"]["rvec"], cfg["lid"]["tvec"] = rv.ravel().tolist(), tv.ravel().tolist()
        cfg["lid"]["reproj_px"] = rms
        cfg["lid"]["pivots_px"] = refined
        self.msg = ("Pivots refined (moved " + ", ".join(f"{k} {v:.1f}px" for k, v in moved.items())
                    + f"), lid fit {rms:.1f} px.")
        self.app.reload(cfg)

    def on_frame(self, frame):
        """Called from the capture thread with each frame."""
        if not self.active:
            return
        if self.step == "intrinsics" and self.collector is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            self.collector.feed(gray)
            if self.collector.done:
                rms, K, dist = self.collector.calibrate(self.cfg["intrinsics"]["K"])
                self.cfg["intrinsics"].update(K=K, dist=dist, calibrated=True, rms=rms)
                self.msg = f"Lens calibrated, error {rms:.2f} px."
                self.collector = None
                self.app.reload(self.cfg)
                self.goto("lid")
        elif self.step == "wiggle":
            self.feed_wiggle()
        elif self.step == "jaw" and self.jaw_samples:
            js = self.jaw_samples
            tt = next((x for x in self.app.tracker.tools if x.tool["id"] == js["tool"]), None)
            if tt is not None and tt.status == "tracking" and "jaw_score" in tt.dbg:
                js["vals"].append(tt.dbg["jaw_score"])
            if time.monotonic() > js["until"]:
                tool = next(t for t in self.cfg["tools"] if t["id"] == js["tool"])
                if js["vals"]:
                    score = float(np.median(js["vals"]))
                    tool[f"jaw_{js['state']}_score"] = score
                    self.msg = f"{tool['colour']} jaw {js['state']}: score {score:.2f}"
                else:
                    self.msg = f"{tool['colour']} stick not tracked; hold it still in view and retry."
                self.jaw_samples = None
                self.app.reload(self.cfg)

    def draw(self, img):
        if not self.active:
            return
        for k, (x, y) in self.clicks.items():
            cv2.drawMarker(img, (int(x), int(y)), (0, 0, 255), cv2.MARKER_CROSS, 18, 2)
            cv2.putText(img, k, (int(x) + 8, int(y) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        for i, (x, y) in enumerate(self.colour_clicks):
            cv2.drawMarker(img, (int(x), int(y)), (255, 0, 255), cv2.MARKER_TILTED_CROSS, 14, 2)
        if self.collector is not None and self.collector.last_corners is not None:
            cv2.drawChessboardCorners(img, self.collector.pattern, self.collector.last_corners, True)
        if self.step == "wiggle":
            for L in self.lines.values():
                for p2, p1, _ in L[-80:]:
                    cv2.line(img, tuple(int(a) for a in p2), tuple(int(a) for a in p1), (0, 255, 255), 1)


class App:
    def __init__(self, cfg, source, config_path=config_mod.CONFIG_PATH, send=True):
        self.cfg = cfg
        self.config_path = config_path
        self.source = source
        self.tracker = Tracker(cfg)
        self.sender = Sender(cfg)
        self.sender.enabled = send
        self.wizard = Wizard(self)
        self.lock = threading.Lock()
        self.keys_lock = threading.Lock()
        self.hw_keys = set()
        self.web_keys = set()
        self.key_reader = KeyReader(self.hw_keys, self.keys_lock)
        self.latest_hsv = None
        self.jpeg = None
        self.jpeg_event = None
        self.stream_clients = 0
        self.last_jpeg_t = 0
        self.fps = RateMeter()
        self.proc_ms = 0.0
        self.capture_latency_ms = 0.0
        self.poses = []
        self.loop = None
        self._stop = False
        self._reload_pending = None

    def reload(self, cfg):
        self._reload_pending = cfg

    # --- capture thread ----------------------------------------------------------------
    def capture_loop(self):
        while not self._stop:
            try:
                frame, ts = self.source.read()
            except Exception:
                log.exception("camera read failed")
                time.sleep(0.5)
                continue
            if self._reload_pending is not None:
                self.cfg = self._reload_pending
                self._reload_pending = None
                self.tracker.reload(self.cfg)
            t0 = time.monotonic()
            with self.keys_lock:
                keys = frozenset(self.hw_keys | self.web_keys)
            poses, hsv = self.tracker.process(frame, t0, keys)
            self.latest_hsv = hsv
            self.poses = poses
            if self.wizard.active:
                try:
                    self.wizard.on_frame(frame)
                except Exception:
                    log.exception("wizard")
            t1 = time.monotonic()
            self.proc_ms = 0.9 * self.proc_ms + 0.1 * (t1 - t0) * 1000
            self.capture_latency_ms = (t1 - ts) * 1000
            self.fps.tick(t1)
            if poses and self.loop is not None:
                self.loop.call_soon_threadsafe(self.sender.push, poses, ts)
            if self.stream_clients and t1 - self.last_jpeg_t >= 1.0 / self.cfg["server"]["mjpeg_fps"]:
                self.last_jpeg_t = t1
                img = frame.copy()
                self.tracker.draw(img)
                self.wizard.draw(img)
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.cfg["server"]["jpeg_quality"]])
                if ok:
                    self.jpeg = buf.tobytes()
                    if self.loop is not None:
                        self.loop.call_soon_threadsafe(self._notify_jpeg)

    def _notify_jpeg(self):
        ev, self.jpeg_event = self.jpeg_event, asyncio.Event()
        if ev is not None:
            ev.set()

    # --- state -------------------------------------------------------------------------
    def state(self):
        tools = []
        for tt in self.tracker.tools:
            d = tt.dbg
            tools.append({
                "id": tt.tool["id"], "hole": tt.tool["hole"], "colour": tt.tool["colour"],
                "port": tt.tool["port"], "hand": tt.tool.get("hand"), "status": tt.status,
                "pose": tt.pose if tt.status in ("tracking", "holding") else None,
                "rms_px": None if d.get("rms") is None else round(d["rms"], 2),
                "depth_phys_cm": None if d.get("depth_phys") is None else round(d["depth_phys"] * 100, 1),
                "tilt_deg": None if d.get("tilt_deg") is None else round(d["tilt_deg"], 1),
                "jaw_score": None if d.get("jaw_score") is None else round(d["jaw_score"], 2),
                "jaw_open": tt.jaw_open, "blobs": len(d.get("blobs", [])),
                "bands_px": [[round(float(c), 1) for c in d[k]] for k in ("b1", "b2") if k in d],
                "stick_length_cm": round(tt.tool["stick"]["length"] * 100, 1),
                "jaw_open_score": tt.tool.get("jaw_open_score"),
                "jaw_closed_score": tt.tool.get("jaw_closed_score"),
            })
        s = self.sender
        with self.keys_lock:
            keys = sorted(self.hw_keys | self.web_keys)
        w = self.wizard
        return {
            "fps": round(self.fps.rate(), 1), "proc_ms": round(self.proc_ms, 2),
            "capture_latency_ms": round(self.capture_latency_ms, 1),
            "send": {"rate": round(s.rate.rate(), 1), "connected": s.connected, "url": self.cfg["sender"]["ws_url"],
                     "udp": self.cfg["sender"].get("udp"), "http": self.cfg["sender"].get("http"),
                     "enabled": s.enabled, "error": s.last_error, "presence": s.presence,
                     "latency_ms": None if s.latency_ms is None else round(s.latency_ms, 1)},
            "tools": tools, "keys": keys, "keyboards": self.key_reader.devices,
            "calibrated": bool(self.cfg["lid"].get("rvec")),
            "lid_rms_px": self.cfg["lid"].get("reproj_px"),
            "intrinsics_calibrated": bool(self.cfg["intrinsics"].get("calibrated")),
            "camera": {k: self.cfg["camera"].get(k) for k in ("width", "height", "lens_position", "exposure_us", "gain")},
            "wizard": {"active": w.active, "step": w.step, "prompt": w.prompt(), "msg": w.msg,
                       "steps": STEPS},
            "image": [self.cfg["camera"]["width"], self.cfg["camera"]["height"]],
        }

    # --- web ---------------------------------------------------------------------------
    async def h_index(self, request):
        return web.FileResponse(os.path.join(STATIC, "index.html"))

    async def h_checker(self, request):
        return web.FileResponse(os.path.join(STATIC, "checker.html"))

    async def h_state(self, request):
        return web.json_response(self.state())

    async def h_snapshot(self, request):
        self.stream_clients += 1
        try:
            for _ in range(50):
                if self.jpeg is not None:
                    break
                await asyncio.sleep(0.05)
        finally:
            self.stream_clients -= 1
        if self.jpeg is None:
            raise web.HTTPServiceUnavailable()
        return web.Response(body=self.jpeg, content_type="image/jpeg")

    async def h_stream(self, request):
        resp = web.StreamResponse(headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frame",
            "Cache-Control": "no-cache"})
        await resp.prepare(request)
        self.stream_clients += 1
        try:
            while True:
                if self.jpeg_event is None:
                    self.jpeg_event = asyncio.Event()
                ev = self.jpeg_event
                try:
                    await asyncio.wait_for(ev.wait(), 2.0)
                except asyncio.TimeoutError:
                    continue
                data = self.jpeg
                await resp.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self.stream_clients -= 1
        return resp

    async def h_cmd(self, request):
        d = await request.json()
        cmd = d.get("cmd")
        if cmd == "key":
            with self.keys_lock:
                (self.web_keys.add if d.get("down") else self.web_keys.discard)(d["code"])
        elif cmd == "keys_clear":
            with self.keys_lock:
                self.web_keys.clear()
        elif cmd == "calib_start":
            self.wizard.start(d.get("step", "focus"))
        elif cmd == "calib_cancel":
            self.wizard.cancel()
        elif cmd == "set_stick_length":
            for t in self.cfg["tools"]:
                if t["id"] == d["tool"]:
                    t["stick"]["length"] = float(d["cm"]) / 100
            config_mod.save(self.cfg, self.config_path)
            self.reload(self.cfg)
        elif cmd == "set_sender":
            for k in ("ws_url", "udp", "http"):
                if k in d:
                    self.cfg["sender"][k] = d[k] or None
            config_mod.save(self.cfg, self.config_path)
            return web.json_response({"ok": True, "note": "restart the service to apply"})
        elif cmd == "send_enable":
            self.sender.enabled = bool(d.get("on", True))
        else:
            # Camera and file work can block for a second; keep the event loop responsive.
            await asyncio.get_running_loop().run_in_executor(None, self.wizard.command, d)
        return web.json_response({"ok": True, "state": self.state()})

    def make_web(self):
        app = web.Application()
        app.router.add_get("/", self.h_index)
        app.router.add_get("/checker", self.h_checker)
        app.router.add_get("/api/state", self.h_state)
        app.router.add_get("/snapshot.jpg", self.h_snapshot)
        app.router.add_get("/stream.mjpg", self.h_stream)
        app.router.add_post("/api/cmd", self.h_cmd)
        return app

    async def main(self):
        self.loop = asyncio.get_running_loop()
        th = threading.Thread(target=self.capture_loop, daemon=True, name="capture")
        th.start()
        runner = web.AppRunner(self.make_web(), access_log=None)
        await runner.setup()
        sv = self.cfg["server"]
        await web.TCPSite(runner, sv["host"], sv["port"]).start()
        log.info("debug page on http://%s:%d/", sv["host"], sv["port"])
        if not self.cfg["lid"].get("rvec"):
            log.info("not calibrated yet: open the debug page and press Calibrate")
        await self.sender.run()
