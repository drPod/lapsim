"""Make labelled synthetic variants of a real frame by digitally retracting stick tips (inpainting the
last part of the shaft), so a "touching" frame yields "near" and "far" negatives.
Usage: python tools/synth_frames.py in.jpg outdir prefix   -> writes jpgs + appends labels.csv rows"""
import csv
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from director.cvcheck import W, H, _masks, _sticks  # noqa: E402


def retract(img, stick, frac):
    """Erase the tip end of a stick over `frac` of its visible length."""
    h, w = img.shape[:2]
    sx, sy = w / W, h / H
    tip, entry = np.array(stick["tip"]), np.array(stick["entry"])
    new_tip = tip + (entry - tip) * frac
    lab = stick["mask"]
    m = (lab > 0).astype(np.uint8)
    # keep only the part of the stick mask beyond new_tip (toward the old tip)
    yy, xx = np.mgrid[0:H, 0:W]
    d = (tip - entry) / np.linalg.norm(tip - entry)
    proj = (xx - new_tip[0]) * d[0] + (yy - new_tip[1]) * d[1]
    erase = (m > 0) & (proj > -2)
    erase = cv2.dilate(erase.astype(np.uint8) * 255, np.ones((9, 9), np.uint8))
    erase = cv2.resize(erase, (w, h), interpolation=cv2.INTER_NEAREST)
    return cv2.inpaint(img, erase, 9, cv2.INPAINT_TELEA)


def main():
    src, out, prefix = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out, exist_ok=True)
    img = cv2.imread(src)
    s = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(s, cv2.COLOR_BGR2GRAY)
    green, bag, orange, h, sat, v = _masks(s)
    sticks, smask = _sticks(gray, green, sat, h)
    n, lab = cv2.connectedComponents(smask)
    for st in sticks:
        st["mask"] = (lab == st["label"]).astype(np.uint8)
        st["side"] = "left" if st["entry"][0] < W / 2 else "right"
    rows = []
    for name, fracs in [("near", 0.08), ("far", 0.3)]:
        for which in ("left", "right", "both"):
            im = img.copy()
            lbl = {"left": "gallbladder", "right": "gallbladder"}
            for st in sticks:
                if which in (st["side"], "both"):
                    im = retract(im, st, fracs)
                    lbl[st["side"]] = "nothing"
            f = f"{prefix}_{name}_{which}.jpg"
            cv2.imwrite(os.path.join(out, f), im, [cv2.IMWRITE_JPEG_QUALITY, 92])
            rows.append([f, lbl["left"], lbl["right"]])
    with open(os.path.join(out, "labels.csv"), "a", newline="") as fh:
        csv.writer(fh).writerows(rows)
    print("\n".join(",".join(r) for r in rows))


if __name__ == "__main__":
    main()
