"""Stick pose from two coloured bands plus the known pivot, and the sim pose conventions."""
import math

import cv2
import numpy as np


def unit(v):
    return v / np.linalg.norm(v)


def cross(a, b):
    # np.cross is slow for single 3-vectors
    return np.array([a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]])


def port_frame(port):
    """Nominal trocar axes, exactly as the sim's tools.js `Tl()` builds them."""
    o = np.asarray(port["pos"], float)
    d = unit(np.asarray(port["aim"], float) - o)
    r = np.cross(d, [0.0, 1.0, 0.0])
    if np.linalg.norm(r) < 1e-6:
        r = np.array([1.0, 0, 0])
    r = unit(r)
    u = np.cross(r, d)
    return o, d, r, u


def sim_angles(port, direction):
    """Inward shaft direction (sim frame) -> (yaw, pitch) like the sim's `Dl()`."""
    _, d, r, u = port_frame(port)
    a = unit(np.asarray(direction, float))
    pitch = math.asin(max(-1.0, min(1.0, float(a @ u))))
    yaw = math.atan2(float(a @ r), float(a @ d))
    return yaw, pitch


def sim_direction(port, yaw, pitch):
    """Inverse of sim_angles (the sim's `El()` shaft direction)."""
    _, d, r, u = port_frame(port)
    return unit(d * math.cos(pitch) * math.cos(yaw) + r * math.cos(pitch) * math.sin(yaw)
                + u * math.sin(pitch))


class Camera:
    """Pinhole camera with lid pose. Points in the lid frame -> pixels."""

    def __init__(self, K, dist, rvec=None, tvec=None):
        self.K = np.asarray(K, float)
        self.dist = np.asarray(dist, float).ravel()
        self.set_pose(rvec, tvec)

    def set_pose(self, rvec, tvec):
        self.rvec = None if rvec is None else np.asarray(rvec, float).reshape(3)
        self.tvec = None if tvec is None else np.asarray(tvec, float).reshape(3)
        if self.rvec is not None:
            self.R = cv2.Rodrigues(self.rvec)[0]
        else:
            self.R = None

    @property
    def posed(self):
        return self.R is not None

    def to_cam(self, X_lid):
        return np.asarray(X_lid, float) @ self.R.T + self.tvec

    def project(self, X_lid):
        X = np.atleast_2d(np.asarray(X_lid, float))
        pts, _ = cv2.projectPoints(X, self.rvec, self.tvec, self.K, self.dist)
        return pts.reshape(-1, 2)

    def normalize(self, px):
        """Pixels -> undistorted normalized image coordinates."""
        px = np.asarray(px, float).reshape(-1, 1, 2)
        return cv2.undistortPoints(px, self.K, self.dist).reshape(-1, 2)

    def lid_pixels_per_metre(self, X_lid):
        p0 = self.project(X_lid)[0]
        p1 = self.project(np.asarray(X_lid) + [0.01, 0, 0])[0]
        p2 = self.project(np.asarray(X_lid) + [0, 0.01, 0])[0]
        return 50 * (np.linalg.norm(p1 - p0) + np.linalg.norm(p2 - p0))


class StickSolver:
    """Find the outward stick direction v and the pivot->band2 distance s.

    band2 = P + s v, band1 = P + (s + L) v, with P the pivot (all in camera coords).
    Minimises reprojection error of both band centres with a tiny Levenberg-Marquardt.
    """

    def __init__(self, cam, pivot_lid, spacing, max_tilt_deg=75, max_s=None):
        self.cam = cam
        # Band 2 can't be farther from the pivot than when the tip sits in the hole.
        self.max_s = max_s
        self.P = cam.to_cam(pivot_lid)
        self.z_lid = cam.R[:, 2]          # lid normal (up) in camera coords
        self.L = spacing
        self.fx = cam.K[0, 0]
        self.min_up = math.cos(math.radians(max_tilt_deg))

    def residual(self, v, s, n2, n1):
        X2 = self.P + s * v
        X1 = self.P + (s + self.L) * v
        if X2[2] <= 1e-4 or X1[2] <= 1e-4:
            return np.full(4, 1e3)
        return self.fx * np.concatenate([X2[:2] / X2[2] - n2, X1[:2] / X1[2] - n1])

    def _lm(self, v, s, n2, n1, iters=12):
        lam = 1e-3
        r = self.residual(v, s, n2, n1)
        cost = r @ r
        for _ in range(iters):
            # local tangent basis around v
            a = cross(v, (1.0, 0.0, 0.0))
            if np.linalg.norm(a) < 0.1:
                a = cross(v, (0.0, 1.0, 0.0))
            a = unit(a)
            b = cross(v, a)

            def f(x):
                return self.residual(unit(v + x[0] * a + x[1] * b), s + x[2], n2, n1)
            J = np.empty((4, 3))
            h = (1e-5, 1e-5, 1e-5)
            for k in range(3):
                dx = np.zeros(3)
                dx[k] = h[k]
                J[:, k] = (f(dx) - r) / h[k]
            A = J.T @ J
            g = J.T @ r
            while True:
                step = -np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-9), g)
                v_new = unit(v + step[0] * a + step[1] * b)
                s_new = max(0.0, s + step[2])
                r_new = self.residual(v_new, s_new, n2, n1)
                c_new = r_new @ r_new
                if c_new < cost:
                    v, s, r, cost = v_new, s_new, r_new, c_new
                    lam = max(lam / 3, 1e-7)
                    break
                lam *= 4
                if lam > 1e6:
                    return v, s, cost
            if np.linalg.norm(step) < 1e-7:
                break
        return v, s, cost

    def candidates(self, n2, n1):
        """All (v, s) consistent with the two band rays, found in the plane they span.

        The stick line, the two band rays and the camera centre are coplanar, so within that
        plane the problem is 1-D: rotate a line about the (projected) pivot until the band
        rays cut it exactly `L` apart. There are usually two roots: the stick leaning towards
        or away from the camera.
        """
        a2 = unit(np.array([n2[0], n2[1], 1.0]))
        a1 = unit(np.array([n1[0], n1[1], 1.0]))
        N = cross(a2, a1)
        if np.linalg.norm(N) < 1e-9:
            return []
        N = unit(N)
        e1 = a2
        e2 = cross(N, e1)
        P = self.P - (self.P @ N) * N
        p = np.array([P @ e1, P @ e2])
        r2 = np.array([1.0, 0.0])
        r1 = np.array([a1 @ e1, a1 @ e2])

        def hit(ray, w):
            # t * ray = p + s * w  ->  [ray, -w] [t, s]^T = p
            det = -ray[0] * w[1] + ray[1] * w[0]
            if abs(det) < 1e-12:
                return None, None
            t = (-p[0] * w[1] + p[1] * w[0]) / det
            s = (ray[0] * p[1] - ray[1] * p[0]) / det
            return t, s

        def f(phi):
            w = np.array([math.cos(phi), math.sin(phi)])
            t2, s2 = hit(r2, w)
            t1, s1 = hit(r1, w)
            if t2 is None or t1 is None or t2 <= 0 or t1 <= 0:
                return None, None
            return (s1 - s2) - self.L, s2

        out = []
        phis = np.linspace(0, 2 * math.pi, 361)
        prev_val = None
        for k, phi in enumerate(phis):
            val, s2 = f(phi)
            if val is not None and prev_val is not None and (val > 0) != (prev_val > 0) \
                    and abs(val - prev_val) < 4 * self.L:
                lo, hi, flo = phis[k - 1], phi, prev_val
                for _ in range(40):
                    mid = 0.5 * (lo + hi)
                    fm, _ = f(mid)
                    if fm is None:
                        break
                    if (fm > 0) == (flo > 0):
                        lo, flo = mid, fm
                    else:
                        hi = mid
                phi_r = 0.5 * (lo + hi)
                _, s2 = f(phi_r)
                if s2 is not None and s2 > -0.01 and (self.max_s is None or s2 <= self.max_s + 0.01):
                    w = math.cos(phi_r) * e1 + math.sin(phi_r) * e2
                    if w @ self.z_lid >= self.min_up:
                        out.append((unit(w), max(0.0, s2)))
            prev_val = val
        return out

    def solve(self, px2, px1, prev=None, accept_px=2.0, prior=None):
        """px2/px1: pixel centres of band2 (lower) and band1. Returns (v_cam, s, rms_px).

        With two mirror-image solutions, keep the one closest to the previous direction, or
        (when starting) closest to `prior`, the port's usual outward direction (camera coords).
        """
        n = self.cam.normalize(np.array([px2, px1]))
        n2, n1 = n[0], n[1]
        if prev is not None:
            v, s, cost = self._lm(np.asarray(prev[0], float), float(prev[1]), n2, n1, iters=6)
            if cost < 4 * accept_px ** 2 and v @ self.z_lid >= self.min_up \
                    and (self.max_s is None or s <= self.max_s + 0.015):
                return v, s, math.sqrt(cost / 4)
        cands = []
        for v0, s0 in self.candidates(n2, n1):
            v, s, cost = self._lm(v0, s0, n2, n1, iters=6)
            if v @ self.z_lid >= self.min_up and (self.max_s is None or s <= self.max_s + 0.015):
                cands.append((v, s, cost))
        if not cands:
            return None
        ref = prev[0] if prev is not None else prior
        if ref is not None and len(cands) > 1:
            best_cost = min(c[2] for c in cands)
            ok = [c for c in cands if c[2] <= max(4 * accept_px ** 2, 4 * best_cost)]
            v, s, cost = max(ok, key=lambda c: c[0] @ ref)
        else:
            v, s, cost = min(cands, key=lambda c: c[2])
        return v, s, math.sqrt(cost / 4)

    def distance_along(self, v, px, fallback):
        """Distance from the pivot along v to the point nearest the pixel's viewing ray."""
        n = self.cam.normalize(np.array([px]))[0]
        a = unit(np.array([n[0], n[1], 1.0]))
        A = np.stack([v, -a], axis=1)
        sol, *_ = np.linalg.lstsq(A, -self.P, rcond=None)
        s = float(sol[0])
        if not (0.0 <= s <= (self.max_s or 1.0) + 0.015) or sol[1] <= 0:
            return fallback
        return s

    def lid_direction(self, v_cam):
        return self.cam.R.T @ v_cam
