"""Per-frame tracking: colour bands -> stick pose -> sim tool pose."""
import math
import time

import cv2
import numpy as np

from . import config as config_mod
from .filters import OneEuro
from .geometry import Camera, StickSolver, port_frame, sim_angles, unit

BGR = {"blue": (255, 120, 0), "green": (0, 200, 0), "red": (0, 0, 255), "pink": (180, 80, 255),
       "orange": (0, 140, 255), "purple": (200, 0, 160)}


def hsv_spec(tool):
    return tool.get("hsv") or config_mod.COLOUR_PRESETS[tool["colour"]]


def colour_mask(hsv, spec):
    s_lo, v_lo = int(spec["s"]), int(spec["v"])
    mask = None
    for lo, hi in spec["h"]:
        m = cv2.inRange(hsv, (int(lo), s_lo, v_lo), (int(hi), 255, 255))
        mask = m if mask is None else cv2.bitwise_or(mask, m)
    return mask


class ToolTracker:
    def __init__(self, cfg, tool, cam):
        self.cfg, self.tool, self.cam = cfg, tool, cam
        f = cfg["filter"]
        self.f_yaw = OneEuro(f["angle_mincutoff"], f["angle_beta"], f["dcutoff"])
        self.f_pitch = OneEuro(f["angle_mincutoff"], f["angle_beta"], f["dcutoff"])
        self.f_depth = OneEuro(f["depth_mincutoff"], f["depth_beta"], f["dcutoff"])
        self.port = cfg["sim_ports"][tool["port"]]
        self.R_box2sim = np.asarray(cfg["box_to_sim"]["rotation"], float)
        st = tool["stick"]
        self.spacing = st["band2_from_top"] - st["band1_from_top"]
        self.tip_to_band2 = st["length"] - st["band2_from_top"]
        self.band1_to_jaw = st["band1_from_top"] - st["jaw_from_top"]
        self.prev = None
        self.win = None
        self.labels = None
        self.last_seen = 0.0
        self.pose = None          # last sent pose dict
        self.status = "no calibration"
        self.jaw_open = True
        self._jaw_votes = 0
        self.dbg = {}
        self.solver = None
        if cam.posed:
            xy = cfg["lid"]["points"][tool["hole"]]
            self.pivot_lid = np.array([xy[0], xy[1], cfg["lid"]["pivot_z"]])
            self.solver = StickSolver(cam, self.pivot_lid, self.spacing,
                                      cfg["tracking"]["max_tilt_deg"], max_s=self.tip_to_band2)
            self.pivot_px = cam.project(self.pivot_lid)[0]
            # Usual outward stick direction: opposite the trocar's nominal inward axis.
            _, d_sim, _, _ = port_frame(self.port)
            self.prior = cam.R @ unit(-(self.R_box2sim.T @ d_sim))
            self.ppm = cam.lid_pixels_per_metre(self.pivot_lid)
            self.roi = self._roi()
            self.status = "searching"

    def _roi(self):
        R = self.cfg["tracking"]["roi_radius_m"]
        pts = [self.pivot_lid]
        for tilt in np.radians([0, 30, 55, 75]):
            for az in np.linspace(0, 2 * np.pi, 12, endpoint=False):
                v = np.array([np.sin(tilt) * np.cos(az), np.sin(tilt) * np.sin(az), np.cos(tilt)])
                pts.append(self.pivot_lid + R * v)
        p = self.cam.project(np.array(pts))
        w, h = self.cfg["camera"]["width"], self.cfg["camera"]["height"]
        x0, y0 = np.clip(p.min(0) - 10, 0, [w, h]).astype(int)
        x1, y1 = np.clip(p.max(0) + 10, 0, [w, h]).astype(int)
        return x0, y0, x1, y1

    def min_area(self):
        a = self.tool.get("band_area")
        base = self.cfg["tracking"]["min_band_area"]
        return max(base, 0.2 * a) if a else base

    def search_window(self):
        """Full ROI while searching; a tight box around the predicted bands while tracking."""
        if self.prev is None:
            return self.roi
        v, s = self.prev
        pts = np.array([self._project_band(v, s), self._project_band(v, s + self.spacing),
                        self._project_band(v, s + self.spacing + self.band1_to_jaw)])
        m = max(30.0, 0.05 * self.ppm)
        x0, y0 = pts.min(0) - m
        x1, y1 = pts.max(0) + m
        rx0, ry0, rx1, ry1 = self.roi
        return (int(max(rx0, x0)), int(max(ry0, y0)), int(min(rx1, x1)), int(min(ry1, y1)))

    def blobs(self, hsv):
        x0, y0, x1, y1 = self.win = self.search_window()
        if x1 - x0 < 4 or y1 - y0 < 4:
            return [], None
        mask = colour_mask(hsv[y0:y1, x0:x1], hsv_spec(self.tool))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, self.labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        out = []
        amin = self.min_area()
        for i in range(1, n):
            a = stats[i, cv2.CC_STAT_AREA]
            if a >= amin:
                out.append((cents[i][0] + x0, cents[i][1] + y0, int(a), i))
        out.sort(key=lambda b: -b[2])
        return out[:6], mask

    def lower_edge(self, blob, d):
        """Point where the band ends on the pivot side. Fingers only ever cover a band from
        the top, so this end is more stable than the centroid."""
        x0, y0 = self.win[0], self.win[1]
        ys, xs = np.nonzero(self.labels == blob[3])
        c = np.array(blob[:2])
        proj = (xs + x0 - c[0]) * d[0] + (ys + y0 - c[1]) * d[1]
        return c + d * np.percentile(proj, 3)

    def _project_band(self, v_cam, s):
        X = self.solver.P + s * v_cam
        pts, _ = cv2.projectPoints(X.reshape(1, 3), np.zeros(3), np.zeros(3), self.cam.K, self.cam.dist)
        return pts.reshape(2)

    def jaw_score(self, mask, v_cam, s):
        st = self.tool["stick"]
        if mask is None:
            return 0.0, None, 0
        dist = s + self.spacing + self.band1_to_jaw
        X = self.solver.P + dist * v_cam
        pts, _ = cv2.projectPoints(X.reshape(1, 3), np.zeros(3), np.zeros(3), self.cam.K, self.cam.dist)
        c = pts.reshape(2)
        r = max(3.0, 0.6 * st["band_height"] * self.cam.K[0, 0] / max(X[2], 0.05))
        x0, y0 = self.win[0], self.win[1]
        cx, cy = c[0] - x0, c[1] - y0
        h, w = mask.shape
        xa, xb = int(max(0, cx - r)), int(min(w, cx + r + 1))
        ya, yb = int(max(0, cy - r)), int(min(h, cy + r + 1))
        if xb <= xa or yb <= ya:
            return 0.0, c, r
        win = mask[ya:yb, xa:xb]
        yy, xx = np.mgrid[ya:yb, xa:xb]
        inside = (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r
        return float((win[inside] > 0).mean()) if inside.any() else 0.0, c, r

    def _pick(self, blobs):
        """Yield (band2, band1) candidate pairs, best guess first."""
        if len(blobs) < 2:
            return
        if self.prev is not None:
            v, s = self.prev
            p2 = self._project_band(v, s)
            p1 = self._project_band(v, s + self.spacing)
            gate = max(25.0, 0.04 * self.ppm)
            b2 = min(blobs, key=lambda b: math.dist(b[:2], p2))
            b1 = min(blobs, key=lambda b: math.dist(b[:2], p1))
            if b1 is not b2 and math.dist(b2[:2], p2) < gate and math.dist(b1[:2], p1) < gate:
                yield b2, b1
        p0 = self.pivot_px
        order = sorted(blobs, key=lambda b: math.dist(b[:2], p0))
        n = len(order)
        for i, j in ((0, 1), (0, 2), (1, 2), (0, 3), (1, 3)):
            if j < n:
                yield order[i], order[j]

    def update(self, hsv, t, keys):
        self.dbg = {"blobs": [], "rms": None}
        if self.solver is None:
            self.status = "no calibration"
            return None
        blobs, mask = self.blobs(hsv)
        self.dbg["blobs"] = blobs
        sol = None
        maxr = self.cfg["tracking"]["max_residual_px"]
        tried = 0
        for b2, b1 in self._pick(blobs):
            tried += 1
            res = self.solver.solve(b2[:2], b1[:2], self.prev, accept_px=0.5 * maxr, prior=self.prior)
            if res is None:
                continue
            v, s, rms = res
            if rms <= maxr and s >= 0.0:
                sol = (v, s, rms, b2, b1)
                break
            if tried >= 4:
                break
        if sol is None:
            if t - self.last_seen > self.cfg["tracking"]["lost_hold_s"]:
                if self.status != "lost" and self.prev is not None:
                    for f in (self.f_yaw, self.f_pitch, self.f_depth):
                        f.reset()
                self.prev = None
                self.status = "lost" if self.last_seen else "searching"
                return None
            self.status = "holding"
            return self.pose

        v, s, rms, b2, b1 = sol
        # Refine on the lower band edges (both 5 cm apart as well, half a band below the centres).
        d = np.array(b1[:2]) - np.array(b2[:2])
        if np.linalg.norm(d) > 4:
            d = d / np.linalg.norm(d)
            e2, e1 = self.lower_edge(b2, d), self.lower_edge(b1, d)
            hb = self.tool["stick"]["band_height"] / 2
            res = self.solver.solve(e2, e1, (v, max(0.0, s - hb)), accept_px=maxr)
            if res is not None and res[2] <= maxr:
                # Direction from the edges; distance from band 2's centre (band 2 is the one
                # next to the lid, away from the fingers), which has no edge-overshoot bias.
                v = res[0]
                s = self.solver.distance_along(v, b2[:2], fallback=res[1] + hb)
                rms = res[2]
                self.dbg.update(e2=e2, e1=e1)
        self.prev = (v, s)
        self.last_seen = t
        self.status = "tracking"
        self.dbg.update(rms=rms, b2=b2[:2], b1=b1[:2])

        # Jaw band
        score, jc, jr = self.jaw_score(mask, v, s)
        self.dbg.update(jaw_px=jc, jaw_r=jr, jaw_score=score)
        o, c = self.tool.get("jaw_open_score"), self.tool.get("jaw_closed_score")
        thr = (o + c) / 2 if (o is not None and c is not None and o - c > 0.1) else 0.25
        want_open = score > thr
        if want_open != self.jaw_open:
            self._jaw_votes += 1
            if self._jaw_votes >= self.cfg["tracking"]["jaw_debounce_frames"]:
                self.jaw_open = want_open
                self._jaw_votes = 0
        else:
            self._jaw_votes = 0

        # Pose in sim terms
        v_lid = self.solver.lid_direction(v)
        inward_sim = self.R_box2sim @ (-v_lid)
        yaw, pitch = sim_angles(self.port, inward_sim)
        d = self.cfg["depth"]
        depth_phys = self.tip_to_band2 - s
        depth = d["scale"] * depth_phys + d["offset"] - d["tip_to_hinge"]
        depth = min(d["max"], max(d["min"], depth))
        yaw = self.f_yaw(yaw, t)
        pitch = self.f_pitch(pitch, t)
        depth = self.f_depth(depth, t)

        hand_close = any(k in keys for k in self.cfg["keys"]["jaw_close_all"]) or any(
            k in keys for k in self.cfg["keys"]["jaw_close"].get(self.tool["id"], []))
        jaw = 0.0 if (hand_close or not self.jaw_open) else 1.0
        etools = self.cfg["keys"].get("energy_tools")
        energy = any(k in keys for k in self.cfg["keys"]["energy"]) and (
            etools is None or self.tool["id"] in etools)
        pose = {"id": self.tool["id"], "port": self.tool["port"], "yaw": round(yaw, 4),
                "pitch": round(pitch, 4), "roll": 0.0, "depth": round(depth, 4),
                "jaw": jaw, "energy": bool(energy)}
        if self.tool.get("tool"):
            pose["tool"] = self.tool["tool"]
        self.pose = pose
        self.dbg["depth_phys"] = depth_phys
        self.dbg["tilt_deg"] = math.degrees(math.acos(max(-1, min(1, v_lid[2]))))
        return pose

    def draw(self, img):
        col = BGR.get(self.tool["colour"], (255, 255, 255))
        if self.solver is None:
            return
        p0 = tuple(int(x) for x in self.pivot_px)
        cv2.circle(img, p0, 7, col, 2)
        cv2.putText(img, self.tool["hole"], (p0[0] + 9, p0[1] + 18), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, col, 2)
        for b in self.dbg.get("blobs", []):
            cv2.circle(img, (int(b[0]), int(b[1])), 4, (255, 255, 255), 1)
        if self.status == "tracking" and self.prev is not None:
            v, s = self.prev
            top = self._project_band(v, s + self.spacing + self.band1_to_jaw)
            cv2.line(img, p0, tuple(int(x) for x in top), col, 2)
            for key in ("b2", "b1"):
                cv2.circle(img, tuple(int(x) for x in self.dbg[key]), 6, col, -1)
            jc = self.dbg.get("jaw_px")
            if jc is not None:
                cv2.circle(img, (int(jc[0]), int(jc[1])), int(self.dbg["jaw_r"]) + 2,
                           (255, 255, 255) if self.jaw_open else (0, 0, 0), 2)
        label = f'{self.tool["id"]}: {self.status}'
        if self.pose and self.status == "tracking":
            label += f' d={self.pose["depth"]*100:.1f}cm'
        cv2.putText(img, label, (p0[0] + 9, p0[1] + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)


class Tracker:
    def __init__(self, cfg):
        self.reload(cfg)

    def reload(self, cfg):
        self.cfg = cfg
        lid = cfg["lid"]
        self.cam = Camera(cfg["intrinsics"]["K"], cfg["intrinsics"]["dist"], lid["rvec"], lid["tvec"])
        self.tools = [ToolTracker(cfg, t, self.cam) for t in cfg["tools"]]

    def process(self, frame_bgr, t, keys=frozenset()):
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        poses = []
        for tt in self.tools:
            p = tt.update(hsv, t, keys)
            if p is not None:
                poses.append(p)
        return poses, hsv

    def draw(self, img):
        if self.cam.posed:
            lid = self.cfg["lid"]
            pts = {k: self.cam.project([v[0], v[1], 0])[0] for k, v in lid["points"].items()}
            c = self.cam.project([[0, 0, 0], [0.05, 0, 0], [0, 0.05, 0], [0, 0, 0.05]])
            c = c.astype(int)
            cv2.line(img, tuple(c[0]), tuple(c[1]), (0, 0, 255), 2)
            cv2.line(img, tuple(c[0]), tuple(c[2]), (0, 255, 0), 2)
            cv2.line(img, tuple(c[0]), tuple(c[3]), (255, 0, 0), 2)
            u = pts.get("U")
            if u is not None:
                cv2.circle(img, tuple(int(x) for x in u), 6, (40, 40, 40), 2)
        for tt in self.tools:
            tt.draw(img)
