"""Calibration helpers: lid pose from clicks, colour sampling, camera intrinsics."""
import math

import cv2
import numpy as np

from .tracker import colour_mask


def solve_lid(cfg, clicks):
    """clicks: {mark: [px, py]} for the lid marks. Returns (rvec, tvec, rms_px)."""
    names = [k for k in cfg["lid"]["click_order"] if k in clicks]
    if len(names) < 4:
        raise ValueError("need 4 marks")
    obj = np.array([[*cfg["lid"]["points"][k], 0.0] for k in names])
    img = np.array([clicks[k] for k in names], float)
    K = np.asarray(cfg["intrinsics"]["K"], float)
    dist = np.asarray(cfg["intrinsics"]["dist"], float)
    best = None
    # OpenCV 5.0's IPPE returns wrong poses for this 4-point layout; SQPnP and the
    # iterative solver are exact, so try both and keep the camera-above-lid solution.
    cands = []
    for flag in (cv2.SOLVEPNP_SQPNP, cv2.SOLVEPNP_ITERATIVE):
        try:
            ok, rv, tv = cv2.solvePnP(obj, img, K, dist, flags=flag)
        except cv2.error:
            continue
        if ok:
            cands.append((rv, tv))
    for rv, tv in cands:
        R = cv2.Rodrigues(rv)[0]
        cam_in_lid = -R.T @ tv.ravel()
        if cam_in_lid[2] <= 0:
            continue
        rv, tv = cv2.solvePnPRefineLM(obj, img, K, dist, rv, tv)
        proj = cv2.projectPoints(obj, rv, tv, K, dist)[0].reshape(-1, 2)
        rms = float(np.sqrt(((proj - img) ** 2).sum(1).mean()))
        if best is None or rms < best[2]:
            best = (rv.ravel(), tv.ravel(), rms)
    if best is None:
        raise ValueError("no solution with the camera above the lid; check the click order")
    return best


def camera_position(rvec, tvec):
    R = cv2.Rodrigues(np.asarray(rvec, float))[0]
    return -R.T @ np.asarray(tvec, float)


def _circ_stats(h):
    """Circular median-ish centre and spread of OpenCV hue values (0..179)."""
    ang = h.astype(float) * (2 * math.pi / 180)
    c = math.atan2(np.sin(ang).mean(), np.cos(ang).mean())
    centre = (c * 180 / (2 * math.pi)) % 180
    d = (h.astype(float) - centre + 90) % 180 - 90
    return centre, float(np.percentile(np.abs(d), 90))


def sample_colour(hsv, points, radius=4):
    """HSV spec from small patches around clicked band centres."""
    vals = []
    for x, y in points:
        x, y = int(round(x)), int(round(y))
        patch = hsv[max(0, y - radius):y + radius + 1, max(0, x - radius):x + radius + 1]
        vals.append(patch.reshape(-1, 3))
    v = np.concatenate(vals)
    # drop the least saturated quarter (edges, glare)
    v = v[v[:, 1] >= np.percentile(v[:, 1], 25)]
    centre, spread = _circ_stats(v[:, 0])
    half = max(8.0, min(22.0, 1.8 * spread + 4))
    lo, hi = centre - half, centre + half
    if lo < 0:
        h = [[0, int(hi)], [int(180 + lo), 179]]
    elif hi > 179:
        h = [[int(lo), 179], [0, int(hi - 180)]]
    else:
        h = [[int(lo), int(hi)]]
    s_lo = int(max(50, 0.45 * np.percentile(v[:, 1], 30)))
    v_lo = int(max(30, 0.4 * np.percentile(v[:, 2], 30)))
    return {"h": h, "s": s_lo, "v": v_lo}


def blob_area_at(hsv, spec, point, radius=40):
    """Area of the colour blob under a clicked point (for min-area thresholds)."""
    x, y = int(point[0]), int(point[1])
    H, W = hsv.shape[:2]
    x0, y0 = max(0, x - radius), max(0, y - radius)
    sub = hsv[y0:min(H, y + radius), x0:min(W, x + radius)]
    m = colour_mask(sub, spec)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    lx, ly = x - x0, y - y0
    if 0 <= ly < lab.shape[0] and 0 <= lx < lab.shape[1] and lab[ly, lx] > 0:
        return int(stats[lab[ly, lx], cv2.CC_STAT_AREA])
    # nearest component
    best = 0
    for i in range(1, n):
        best = max(best, int(stats[i, cv2.CC_STAT_AREA]))
    return best


class IntrinsicsCollector:
    """Collect checkerboard views (phone/laptop screen) and calibrate."""

    def __init__(self, size, pattern=(9, 6), target=15):
        self.size = size
        self.pattern = pattern
        self.target = target
        self.objp = np.zeros((pattern[0] * pattern[1], 3), np.float32)
        self.objp[:, :2] = np.mgrid[0:pattern[0], 0:pattern[1]].T.reshape(-1, 2)
        self.views = []
        self.last_corners = None

    def feed(self, gray):
        small = cv2.resize(gray, None, fx=0.5, fy=0.5)
        ok, corners = cv2.findChessboardCorners(
            small, self.pattern, cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_FAST_CHECK)
        self.last_corners = None
        if not ok:
            return False
        corners = corners * 2
        corners = cv2.cornerSubPix(gray, corners, (5, 5), (-1, -1),
                                   (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01))
        self.last_corners = corners
        c = corners.reshape(-1, 2)
        sig = np.array([*c.mean(0), np.linalg.norm(c[0] - c[-1])])
        for v in self.views:
            if np.linalg.norm(v[1] - sig) < 0.06 * self.size[0]:
                return False
        self.views.append((corners, sig))
        return True

    @property
    def done(self):
        return len(self.views) >= self.target

    def calibrate(self, K0):
        obj = [self.objp] * len(self.views)
        imgp = [v[0] for v in self.views]
        flags = cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_ASPECT_RATIO | cv2.CALIB_ZERO_TANGENT_DIST | cv2.CALIB_FIX_K3
        rms, K, dist, _, _ = cv2.calibrateCamera(obj, imgp, self.size, np.asarray(K0, np.float64),
                                                 np.zeros(5), flags=flags)
        return float(rms), K.tolist(), dist.ravel().tolist()
