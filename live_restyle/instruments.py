"""Instrument (chopstick) segmentation on the raw frame + a guide shader for them.

Sticks are found by HSV thresholds (config `instruments.hsv`: list of
[hmin,hmax,smin,smax,vmin,vmax] ranges, OpenCV hue 0..180). Components must
touch the frame border (instruments enter from outside). The far end of each
component is the tip; the last `tip_frac` of its length gets a metal colour so
the diffusion pass renders grasper jaws there.
"""
from __future__ import annotations

import cv2
import numpy as np


def segment(img_bgr: np.ndarray, cfg: dict, work_w: int = 320) -> tuple[np.ndarray, np.ndarray]:
    """Returns (shaft_mask, tip_mask) float32 at img resolution, 0..1."""
    H, W = img_bgr.shape[:2]
    h = int(round(H * work_w / W))
    small = cv2.resize(img_bgr, (work_w, h), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    m = np.zeros((h, work_w), np.uint8)
    for r in cfg.get("hsv", [[0, 180, 0, 110, 0, 70]]):
        lo = np.array([r[0], r[2], r[4]], np.uint8)
        hi = np.array([r[1], r[3], r[5]], np.uint8)
        m |= cv2.inRange(hsv, lo, hi)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    shaft = np.zeros((h, work_w), np.float32)
    tip = np.zeros((h, work_w), np.float32)
    min_area = cfg.get("min_area_frac", 0.004) * h * work_w
    tip_frac = cfg.get("tip_frac", 0.14)
    border = cfg.get("border_px", 4)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < min_area:
            continue
        ys, xs = np.nonzero(lab == i)
        d = np.minimum.reduce([xs, work_w - 1 - xs, ys, h - 1 - ys])
        if d.min() > border:
            continue
        k = np.argmin(d)
        ex, ey = xs[k], ys[k]
        dist = np.sqrt((xs - ex) ** 2.0 + (ys - ey) ** 2.0)
        L = dist.max()
        if L < 0.15 * work_w:
            continue  # stubby blob, probably shadow
        shaft[ys, xs] = 1.0
        sel = dist > L * (1 - tip_frac)
        tip[ys[sel], xs[sel]] = 1.0
    dil = cfg.get("dilate_px", 2)
    if dil:
        k = np.ones((2 * dil + 1, 2 * dil + 1), np.uint8)
        shaft = cv2.dilate(shaft, k)
        tip = cv2.dilate(tip, k)
    shaft = cv2.resize(cv2.GaussianBlur(shaft, (0, 0), 0.8), (W, H), interpolation=cv2.INTER_LINEAR)
    tip = cv2.resize(cv2.GaussianBlur(tip, (0, 0), 0.8), (W, H), interpolation=cv2.INTER_LINEAR)
    return shaft, np.minimum(tip, shaft)
