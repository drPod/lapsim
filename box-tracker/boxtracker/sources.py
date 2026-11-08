"""Frame sources: the Pi camera, or a synthetic scene for testing without hardware."""
import math
import threading
import time

import numpy as np


class PiCamSource:
    def __init__(self, cfg):
        from picamera2 import Picamera2
        c = cfg["camera"]
        self.cam = Picamera2()
        controls = {"FrameRate": c["fps"]}
        conf = self.cam.create_video_configuration(
            main={"size": (c["width"], c["height"]), "format": "RGB888"},
            sensor={"output_size": tuple(c["sensor_size"]), "bit_depth": 10},
            controls=controls, buffer_count=4, queue=False)
        if c.get("hflip") or c.get("vflip"):
            from libcamera import Transform
            conf["transform"] = Transform(hflip=int(bool(c.get("hflip"))), vflip=int(bool(c.get("vflip"))))
        self.cam.configure(conf)
        self.cam.start()
        self.apply_locked(c)
        self.lock = threading.Lock()
        self.last_meta = {}

    def apply_locked(self, c):
        ctl = {}
        if c.get("lens_position") is not None:
            ctl.update(AfMode=0, LensPosition=float(c["lens_position"]))   # manual
        else:
            ctl.update(AfMode=2)   # continuous until the Focus step locks it
        if c.get("exposure_us"):
            ctl.update(AeEnable=False, ExposureTime=int(c["exposure_us"]),
                       AnalogueGain=float(c["gain"] or 4.0))
        else:
            ctl.update(AeEnable=True)
        if c.get("colour_gains"):
            ctl.update(AwbEnable=False, ColourGains=tuple(c["colour_gains"]))
        else:
            ctl.update(AwbEnable=True)
        self.cam.set_controls(ctl)

    def read(self):
        req = self.cam.capture_request()
        try:
            frame = req.make_array("main")
            md = req.get_metadata()
        finally:
            req.release()
        self.last_meta = md
        ts = md.get("SensorTimestamp")
        # SensorTimestamp shares CLOCK_MONOTONIC with time.monotonic_ns() on the Pi 5.
        return frame, (ts / 1e9 if ts else time.monotonic())

    def autofocus_and_lock(self, max_exposure_us=10000):
        """One AF sweep, then freeze focus, exposure and white balance. Returns settings."""
        self.cam.set_controls({"AfMode": 1, "AeEnable": True, "AwbEnable": True})
        time.sleep(0.3)
        ok = self.cam.autofocus_cycle()
        time.sleep(1.0)   # let AE/AWB settle
        md = self.cam.capture_metadata()
        exp = int(md.get("ExposureTime", 10000))
        gain = float(md.get("AnalogueGain", 2.0))
        if exp > max_exposure_us:   # short exposures keep moving sticks sharp
            gain = min(16.0, gain * exp / max_exposure_us)
            exp = max_exposure_us
        settings = {"lens_position": float(md.get("LensPosition", 2.0)), "exposure_us": exp,
                    "gain": round(gain, 3),
                    "colour_gains": [round(float(x), 4) for x in md.get("ColourGains", (1.8, 1.8))]}
        self.apply_locked(settings)
        settings["af_ok"] = bool(ok)
        return settings

    def unlock(self):
        self.apply_locked({})

    def close(self):
        self.cam.stop()
        self.cam.close()


class SynthSource:
    """Animated synthetic box; behaves like a camera (paced to `fps`)."""

    def __init__(self, cfg, fps=56):
        from .synth import SynthScene
        self.scene = SynthScene(cfg, eye=(0.0, -0.02, 0.50), target=(0, 0, 0), up=(0, 1, 0))
        self.cfg = cfg
        self.dt = 1.0 / fps
        self.t0 = time.monotonic()
        self.next = self.t0
        self.last_meta = {}

    def read(self):
        from .synth import wobble
        now = time.monotonic()
        if now < self.next:
            time.sleep(self.next - now)
        self.next = max(self.next + self.dt, time.monotonic() - self.dt)
        t = time.monotonic() - self.t0
        sticks = []
        for i, tool in enumerate(self.cfg["tools"]):
            v, ins = wobble(t, i)
            sticks.append({"tool": tool, "dir": v, "insertion": ins,
                           "jaw_covered": int(t / 2 + i) % 2 == 1})
        ts = time.monotonic()
        return self.scene.render(sticks), ts

    def autofocus_and_lock(self, **_):
        return {"lens_position": 2.0, "exposure_us": 8000, "gain": 2.0,
                "colour_gains": [1.8, 1.8], "af_ok": True}

    def apply_locked(self, c):
        pass

    def unlock(self):
        pass

    def close(self):
        pass
