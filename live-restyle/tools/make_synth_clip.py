#!/usr/bin/env python3
"""Make a synthetic test clip from one still of the box.

The camera is fixed under the lid, so the clip keeps the scene almost static
(tiny breathing/handling drift) and animates the instruments: dark sticks are
segmented, the background behind them is inpainted, and each stick is
re-composited rotating about its entry point and sliding in/out, while the
tissue near the tips is pushed around with a smooth local warp.

  python tools/make_synth_clip.py in.png out.mp4 --seconds 12 --size 1024x576
"""
import argparse
import math

import cv2
import numpy as np


def stick_masks(img, vmax, smax, min_area):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 2] < vmax) & (hsv[..., 1] < smax)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    h, w = m.shape
    out = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < min_area:
            continue
        comp = (lab == i).astype(np.uint8)
        ys, xs = np.nonzero(comp)
        # entry point = component pixel closest to the image border
        d = np.minimum.reduce([xs, w - 1 - xs, ys, h - 1 - ys])
        if d.min() > 10:
            continue  # not touching the border → not an instrument
        k = np.argmin(d)
        entry = (float(xs[k]), float(ys[k]))
        far = np.argmax((xs - entry[0]) ** 2 + (ys - entry[1]) ** 2)
        tip = (float(xs[far]), float(ys[far]))
        out.append((cv2.dilate(comp, np.ones((7, 7), np.uint8)), entry, tip))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp")
    ap.add_argument("out")
    ap.add_argument("--seconds", type=float, default=12)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--size", default="1024x576")
    ap.add_argument("--vmax", type=int, default=75, help="max HSV value for stick pixels")
    ap.add_argument("--smax", type=int, default=110)
    ap.add_argument("--amp", type=float, default=6.0, help="stick rotation amplitude (deg)")
    ap.add_argument("--save-masks", default=None)
    args = ap.parse_args()
    W, H = map(int, args.size.split("x"))
    img = cv2.resize(cv2.imread(args.inp), (W, H), interpolation=cv2.INTER_AREA)

    sticks = stick_masks(img, args.vmax, args.smax, (W * H) // 400)
    print(f"found {len(sticks)} instrument(s)")
    allm = np.zeros((H, W), np.uint8)
    for m, _, _ in sticks:
        allm |= m
    if args.save_masks:
        cv2.imwrite(args.save_masks, allm * 255)
    plate = cv2.inpaint(img, allm * 255, 9, cv2.INPAINT_TELEA) if sticks else img
    plate = cv2.GaussianBlur(plate, (0, 0), 0.8)

    vw = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), args.fps, (W, H))
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    n = int(args.seconds * args.fps)
    for i in range(n):
        t = i / args.fps
        frame = plate.copy()
        # tissue pushed by tips: smooth local displacement following the tip motion
        dx = np.zeros((H, W), np.float32)
        dy = np.zeros((H, W), np.float32)
        layers = []
        for j, (m, entry, tip) in enumerate(sticks):
            ang = args.amp * math.sin(2 * math.pi * t / (3.1 + 1.3 * j) + j)
            slide = 0.06 * math.sin(2 * math.pi * t / (4.3 + 0.7 * j) + 2 * j)
            ex, ey = entry
            vx, vy = tip[0] - ex, tip[1] - ey
            M = cv2.getRotationMatrix2D((ex, ey), ang, 1.0)
            M[0, 2] += vx * slide
            M[1, 2] += vy * slide
            ms = cv2.warpAffine(m.astype(np.float32), M, (W, H))
            ps = cv2.warpAffine(img, M, (W, H))
            layers.append((ms, ps))
            ntx, nty = M @ np.array([tip[0], tip[1], 1.0])
            r2 = ((xx - ntx) ** 2 + (yy - nty) ** 2) / (0.08 * W) ** 2
            g = np.exp(-r2)
            dx += g * (ntx - tip[0]) * 0.5
            dy += g * (nty - tip[1]) * 0.5
        # handling drift of the whole box (sub-pixel to a few px)
        gx = 2.0 * math.sin(2 * math.pi * t / 7.0)
        gy = 1.5 * math.sin(2 * math.pi * t / 9.0 + 1)
        frame = cv2.remap(frame, xx - dx - gx, yy - dy - gy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        for ms, ps in layers:
            ms = cv2.GaussianBlur(ms, (0, 0), 1.0)[..., None]
            ps = cv2.remap(ps, xx - gx, yy - gy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            ms = cv2.remap(ms, xx - gx, yy - gy, cv2.INTER_LINEAR)[..., None]
            frame = (frame * (1 - ms) + ps * ms).astype(np.uint8)
        # sensor noise so temporal stability is tested against real-ish input
        noise = np.random.default_rng(i).normal(0, 2.0, frame.shape)
        vw.write(np.clip(frame + noise, 0, 255).astype(np.uint8))
    vw.release()
    print("wrote", args.out, n, "frames")


if __name__ == "__main__":
    main()
