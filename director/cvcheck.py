"""Cheap colour/shape contact pre-check for the box trainer.

Finds the green bag (gallbladder), the orange and clear tubes (ducts) and the
dark chopsticks (instruments), locates each stick tip, and measures how far
each tip is from the bag and the tubes. Runs in a few ms on a 512 px frame, so
it gives the director a fast contact signal that the VLM confirms or enriches.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import cv2
import numpy as np

W, H = 512, 288  # working resolution (16:9, like the Pi camera)


@dataclass
class Tip:
    x: float  # normalised 0..1
    y: float
    side: str  # "left" / "right" (which edge the stick enters from)
    length: float  # visible shaft length, normalised by frame width
    dist_bag: float  # px at working res from tip to bag mask (0 = inside)
    dist_orange: float
    dist_clear: float
    on: str  # "bag" | "orange_tube" | "clear_tube" | "none"


@dataclass
class CVResult:
    bag_found: bool
    bag_area: float
    bag_centroid: tuple[float, float] | None
    tips: list[Tip] = field(default_factory=list)
    contact_bag: bool = False
    contact_tube: str | None = None
    ms: float = 0.0

    def to_dict(self):
        d = asdict(self)
        d["tips"] = [asdict(t) for t in self.tips]
        return d


def _masks(s: np.ndarray):
    hsv = cv2.cvtColor(s, cv2.COLOR_BGR2HSV)
    h, sat, v = [c.astype(np.int16) for c in cv2.split(hsv)]
    b, g, r = [c.astype(np.int16) for c in cv2.split(s)]
    green = (h > 35) & (h < 95) & (sat > 60) & ((g - np.maximum(r, b)) > 8)
    green = green.astype(np.uint8) * 255
    green = cv2.morphologyEx(green, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    green = cv2.morphologyEx(green, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    # keep the largest green blob = the bag
    n, lab, st, cen = cv2.connectedComponentsWithStats(green)
    bag = np.zeros_like(green)
    if n > 1:
        i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        if st[i, cv2.CC_STAT_AREA] > 0.01 * W * H:
            bag[lab == i] = 255
            # fill holes (specular highlights on the plastic)
            cnts, _ = cv2.findContours(bag, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(bag, cnts, -1, 255, -1)
    orange = ((h >= 5) & (h <= 25) & (sat > 90) & (v > 90)).astype(np.uint8) * 255
    orange = cv2.morphologyEx(orange, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return green, bag, orange, h, sat, v


def _sticks(gray: np.ndarray, green: np.ndarray, sat: np.ndarray, h: np.ndarray):
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (45, 45))
    bh = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k)
    orange_ish = (h >= 5) & (h <= 25) & (sat > 90)
    m = ((bh > 30) & (gray < 110) & (green == 0) & ~orange_ish).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    sticks = []
    for i in range(1, n):
        x, y, w, hh, area = st[i]
        if area < 250:
            continue
        touches = x <= 2 or y <= 2 or x + w >= W - 2 or y + hh >= H - 2
        if not touches:
            continue
        ys, xs = np.where(lab == i)
        pts = np.stack([xs, ys], 1).astype(np.float32)
        mean, evecs, evals = cv2.PCACompute2(pts, None)
        elong = float(np.sqrt(evals[0, 0] / max(evals[1, 0], 1e-3)))
        if elong < 4.0:
            continue
        d = evecs[0]
        proj = (pts - mean) @ d
        # the end of the shaft nearest the frame border is the entry; the other end is the tip
        p_lo, p_hi = pts[np.argmin(proj)], pts[np.argmax(proj)]

        def border_dist(p):
            return min(p[0], p[1], W - 1 - p[0], H - 1 - p[1])

        entry, tip = (p_lo, p_hi) if border_dist(p_lo) < border_dist(p_hi) else (p_hi, p_lo)
        # refine tip: median of the few pixels furthest along the axis toward the tip
        sign = 1.0 if (tip - mean[0]) @ d > 0 else -1.0
        far = pts[np.argsort(sign * proj)[-12:]]
        tip = np.median(far, 0)
        sticks.append(dict(tip=tip, entry=entry, length=float(np.ptp(proj)), area=int(area), label=i))
    # two instruments at most; keep the longest shafts
    sticks.sort(key=lambda s: -s["length"])
    return sticks[:2], m


def analyse(img_bgr: np.ndarray, contact_px: float = 6.0, debug: bool = False):
    import time

    t0 = time.perf_counter()
    s = cv2.resize(img_bgr, (W, H), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(s, cv2.COLOR_BGR2GRAY)
    green, bag, orange, h, sat, v = _masks(s)
    # clear tube: bright, unsaturated, elongated - hard to segment; approximate as
    # low-sat bright pixels near the bottom half that are not background beige
    sticks, smask = _sticks(gray, green, sat, h)

    def dist_map(mask):
        if mask.any():
            return cv2.distanceTransform(255 - mask, cv2.DIST_L2, 3)
        return None

    db, do = dist_map(bag), dist_map(orange)
    area = float(bag.sum() / 255) / (W * H)
    cen = None
    if area > 0:
        M = cv2.moments(bag, binaryImage=True)
        cen = (M["m10"] / M["m00"] / W, M["m01"] / M["m00"] / H)
    res = CVResult(bag_found=area > 0, bag_area=round(area, 4), bag_centroid=cen)
    for st in sticks:
        tx, ty = [int(round(c)) for c in st["tip"]]
        tx, ty = min(max(tx, 0), W - 1), min(max(ty, 0), H - 1)
        dbag = float(db[ty, tx]) if db is not None else 999.0
        dor = float(do[ty, tx]) if do is not None else 999.0
        on = "none"
        if dbag <= contact_px:
            on = "bag"
        elif dor <= contact_px:
            on = "orange_tube"
        res.tips.append(Tip(x=tx / W, y=ty / H, side="left" if st["entry"][0] < W / 2 else "right",
                            length=st["length"] / W, dist_bag=round(dbag, 1), dist_orange=round(dor, 1),
                            dist_clear=999.0, on=on))
    res.contact_bag = any(t.on == "bag" for t in res.tips)
    tube = [t.on for t in res.tips if t.on.endswith("_tube")]
    res.contact_tube = tube[0] if tube else None
    res.ms = round((time.perf_counter() - t0) * 1000, 1)
    if not debug:
        return res
    vis = s.copy()
    vis[bag > 0] = (0.5 * vis[bag > 0] + (0, 120, 0)).astype(np.uint8)
    vis[orange > 0] = (0.5 * vis[orange > 0] + (0, 60, 120)).astype(np.uint8)
    vis[smask > 0] = (0.5 * vis[smask > 0] + (120, 0, 120)).astype(np.uint8)
    for t in res.tips:
        c = (0, 0, 255) if t.on == "bag" else (0, 165, 255) if t.on != "none" else (255, 255, 255)
        cv2.circle(vis, (int(t.x * W), int(t.y * H)), 6, c, 2)
        cv2.putText(vis, f"{t.on} {t.dist_bag:.0f}", (int(t.x * W) + 6, int(t.y * H) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, c, 1)
    return res, vis


if __name__ == "__main__":
    import json
    import sys

    for p in sys.argv[1:]:
        img = cv2.imread(p)
        r, vis = analyse(img, debug=True)
        print(p, json.dumps(r.to_dict()))
        cv2.imwrite(p.rsplit(".", 1)[0] + ".cv.png", vis)
