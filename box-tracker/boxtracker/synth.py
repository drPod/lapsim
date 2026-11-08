"""Synthetic box renderer: a white lid, marks, and banded sticks seen by a virtual camera.

Used by the tests and by `boxtracker run --source synth` to exercise everything without
hardware.
"""
import math

import cv2
import numpy as np

from .geometry import unit

PAINT = {"blue": (200, 90, 20), "green": (40, 160, 30), "red": (30, 30, 210),
         "pink": (150, 60, 230), "orange": (20, 120, 240), "purple": (150, 30, 120)}
WOOD = (150, 190, 215)
SKIN = (120, 150, 205)


def look_at(eye, target, up=(0, 0, 1)):
    """rvec/tvec of a camera at `eye` looking at `target` (lid frame)."""
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    z = unit(target - eye)
    x = unit(np.cross(z, up))
    y = np.cross(z, x)
    R = np.stack([x, y, z])       # world -> camera rows
    t = -R @ eye
    return cv2.Rodrigues(R)[0].ravel(), t


class SynthScene:
    def __init__(self, cfg, eye=(0.0, 0.30, 0.42), target=(0.0, -0.01, 0.0), seed=0, up=(0, 0, 1)):
        self.cfg = cfg
        c = cfg["camera"]
        self.w, self.h = c["width"], c["height"]
        self.K = np.asarray(cfg["intrinsics"]["K"], float)
        self.dist = np.zeros(5)
        self.rvec, self.tvec = look_at(eye, target, up)
        self.R = cv2.Rodrigues(self.rvec)[0]
        self.rng = np.random.default_rng(seed)

    def proj(self, X):
        X = np.atleast_2d(np.asarray(X, float))
        p, _ = cv2.projectPoints(X, self.rvec, self.tvec, self.K, self.dist)
        return p.reshape(-1, 2)

    def zcam(self, X):
        return (self.R @ np.asarray(X, float) + self.tvec)[2]

    def stick_points(self, tool, direction_out, insertion):
        """Pivot, unit outward direction, insertion depth (pivot->tip) -> band centres."""
        st = tool["stick"]
        xy = self.cfg["lid"]["points"][tool["hole"]]
        P = np.array([xy[0], xy[1], self.cfg["lid"]["pivot_z"]])
        v = unit(np.asarray(direction_out, float))
        top = P + (st["length"] - insertion) * v
        along = lambda from_top: top - from_top * v
        return P, v, top, along

    def render(self, sticks, noise=3.0, hands=True):
        """sticks: list of dict(tool, dir, insertion, jaw_covered)."""
        img = np.full((self.h, self.w, 3), (70, 75, 80), np.uint8)
        # lid
        lid = np.array([[-0.14, -0.115, 0], [0.14, -0.115, 0], [0.14, 0.115, 0], [-0.14, 0.115, 0]])
        cv2.fillConvexPoly(img, self.proj(lid).astype(np.int32), (225, 230, 232))
        pts = self.cfg["lid"]["points"]
        for k, (x, y) in pts.items():
            p = self.proj([x, y, 0])[0]
            r = max(2, int(0.005 * self.K[0, 0] / self.zcam([x, y, 0])))
            cv2.circle(img, tuple(int(a) for a in p), r, (25, 25, 25) if k == "U" else (60, 50, 45), -1)
        # sticks, far to near
        items = []
        for s in sticks:
            tool = s["tool"]
            P, v, top, along = self.stick_points(tool, s["dir"], s["insertion"])
            items.append((self.zcam(P + 0.08 * v), s, P, v, top, along))
        items.sort(key=lambda it: -it[0])
        for _, s, P, v, top, along in items:
            st = s["tool"]["stick"]
            col = PAINT[s["tool"]["colour"]]
            def seg(a, b, color, extra=0.0):
                pa, pb = self.proj([a, b])
                z = (self.zcam(a) + self.zcam(b)) / 2
                th = max(1, int(round((st["diameter"] + extra) * self.K[0, 0] / z)))
                cv2.line(img, tuple(int(x) for x in pa), tuple(int(x) for x in pb), color, th,
                         cv2.LINE_AA)
            seg(P, top, WOOD)
            hb = st["band_height"] / 2
            for key in ("band2_from_top", "band1_from_top"):
                seg(along(st[key] - hb), along(st[key] + hb), col, 0.001)
            seg(along(0.0), along(st["jaw_from_top"] + hb), col, 0.001)
            if hands:
                # fingers on the grip zone; thumb over the jaw band when closing
                seg(along(0.02), along(0.065), SKIN, 0.012)
                if s.get("jaw_covered"):
                    seg(along(-0.008), along(0.016), SKIN, 0.010)
        img = cv2.GaussianBlur(img, (3, 3), 0.8)
        if noise:
            img = np.clip(img + self.rng.normal(0, noise, img.shape), 0, 255).astype(np.uint8)
        return img

    def lid_clicks(self):
        return {k: self.proj([x, y, 0])[0].tolist() for k, (x, y) in self.cfg["lid"]["points"].items()}


def wobble(t, i):
    """A smooth, plausible stick motion for demos/tests: (outward dir, insertion)."""
    tilt = math.radians(18 + 14 * math.sin(0.7 * t + i))
    az = 1.3 * t + 2.1 * i
    v = np.array([math.sin(tilt) * math.cos(az), math.sin(tilt) * math.sin(az), math.cos(tilt)])
    ins = 0.08 + 0.035 * math.sin(0.9 * t + 1.7 * i)
    return v, ins
