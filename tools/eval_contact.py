"""Contact-detection accuracy of the CV pre-check vs the VLM observer on a labelled frame set.

labels.csv columns: file,left,right   (values: gallbladder | orange_tube | clear_tube | nothing | absent)
Usage: python -m tools.eval_contact samples/labelled/labels.csv [--vlm http://127.0.0.1:8001/v1] [--out results.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from director.cvcheck import analyse  # noqa: E402
from director.engine import _fuse_one  # noqa: E402
from director.vlm import VLM  # noqa: E402

CVMAP = {"bag": "gallbladder", "orange_tube": "orange_tube"}


def frame_bag(left, right):
    return "gallbladder" in (left, right)


def frame_tube(left, right):
    return any(x in ("orange_tube", "clear_tube") for x in (left, right))


def confusion(pairs):
    tp = sum(1 for g, p in pairs if g and p)
    tn = sum(1 for g, p in pairs if not g and not p)
    fp = sum(1 for g, p in pairs if not g and p)
    fn = sum(1 for g, p in pairs if g and not p)
    n = len(pairs) or 1
    return {"n": len(pairs), "acc": round((tp + tn) / n, 3), "tp": tp, "tn": tn, "fp": fp, "fn": fn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("labels")
    ap.add_argument("--vlm", help="OpenAI base url of the vLLM server")
    ap.add_argument("--model", default="nemotron-nano-vl")
    ap.add_argument("--out")
    a = ap.parse_args()
    root = os.path.dirname(a.labels)
    rows = list(csv.DictReader(open(a.labels)))
    vlm = VLM({"base_url": a.vlm, "model": a.model, "timeout_s": 60}) if a.vlm else None
    per = []
    for r in rows:
        img = cv2.imread(os.path.join(root, r["file"]))
        cv = analyse(img)
        cvs = {"left": "absent", "right": "absent"}
        for t in cv.tips:
            cvs.setdefault(t.side, "absent")
            if cvs[t.side] == "absent":
                cvs[t.side] = CVMAP.get(t.on, "nothing")
        rec = {"file": r["file"], "gt": {"left": r["left"], "right": r["right"]}, "cv": cvs, "cv_ms": cv.ms}
        if vlm:
            t0 = time.perf_counter()
            obs = vlm.observe(img)
            rec["vlm_ms"] = round((time.perf_counter() - t0) * 1000)
            rec["vlm"] = {s: ((obs.get(s) or {}).get("touching", "nothing") if (obs.get(s) or {}).get("visible", True)
                              else "absent") for s in ("left", "right")}
            rec["vlm"] = {s: ("nothing" if v == "liver" else v) for s, v in rec["vlm"].items()}
            rec["vlm_raw"] = obs
            for mode in ("and", "or"):
                rec[mode] = {s: _fuse_one(mode, None if cvs[s] == "absent" else cvs[s],
                                          None if rec["vlm"][s] == "absent" else rec["vlm"][s]) for s in ("left", "right")}
        per.append(rec)
        print(r["file"], "gt", rec["gt"], "cv", cvs, "vlm", rec.get("vlm"), rec.get("vlm_ms"), flush=True)

    def norm(x):
        return "nothing" if x == "absent" else x

    report = {}
    for det in ["cv"] + (["vlm", "and", "or"] if vlm else []):
        bag_frame = [(frame_bag(*r["gt"].values()), frame_bag(*r[det].values())) for r in per]
        tube_frame = [(frame_tube(*r["gt"].values()), frame_tube(*r[det].values())) for r in per]
        tip_exact = [norm(r["gt"][s]) == norm(r[det][s]) for r in per for s in ("left", "right")]
        tip_bag = [(norm(r["gt"][s]) == "gallbladder", norm(r[det][s]) == "gallbladder") for r in per for s in ("left", "right")]
        report[det] = {"frame_bag_contact": confusion(bag_frame), "frame_tube_contact": confusion(tube_frame),
                       "tip_bag_contact": confusion(tip_bag),
                       "tip_label_exact_acc": round(sum(tip_exact) / max(len(tip_exact), 1), 3)}
    report["latency_ms"] = {"cv_median": statistics.median(r["cv_ms"] for r in per)}
    if vlm:
        ms = [r["vlm_ms"] for r in per]
        report["latency_ms"].update(vlm_median=statistics.median(ms), vlm_p90=sorted(ms)[int(0.9 * (len(ms) - 1))])
    print(json.dumps(report, indent=1))
    if a.out:
        json.dump({"report": report, "frames": per}, open(a.out, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
