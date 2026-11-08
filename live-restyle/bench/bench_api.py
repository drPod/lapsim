#!/usr/bin/env python3
"""Benchmark API edit engines (gemini | openai) on stills: s/frame, $/frame and drift.

Drift = mean abs RGB difference (0..1, at 128x72) between consecutive outputs for the SAME input
frame, i.e. pure model flicker with no scene motion.

python bench/bench_api.py --engine gemini --models gemini-nano-banana-2.1,gemini-3.1-flash-image \
    --inputs runs/pi_still.jpg --repeats 4 [--use-previous] [--event '{"type":"bleed","severity":0.7}']
"""
import argparse
import concurrent.futures as cf
import json
import os
import sys
import time

import cv2
import numpy as np
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_restyle.events import EventState  # noqa: E402


def small(img):
    return cv2.resize(img, (128, 72), interpolation=cv2.INTER_AREA).astype(np.float32) / 255


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/restyle.yaml")
    ap.add_argument("--engine", default="gemini")
    ap.add_argument("--models", default=None)
    ap.add_argument("--set", default="{}", help="JSON overrides for the engine section")
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--use-previous", action="store_true")
    ap.add_argument("--parallel", type=int, default=1, help="concurrent requests (latency under load)")
    ap.add_argument("--event", default=None)
    ap.add_argument("--out", default="runs/bench_api")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config))
    os.makedirs(args.out, exist_ok=True)
    ev = EventState(cfg.get("events", {}))
    if args.event:
        ev.apply(json.loads(args.event))
        for e in ev.events.values():
            e.t0 -= 10
    if args.engine == "gemini":
        from live_restyle.engines.gemini_edit import GeminiEditEngine as E
    else:
        from live_restyle.engines.openai_edit import OpenAIEditEngine as E
    sec = args.engine
    models = (args.models or cfg[sec]["model"]).split(",")
    summary = []
    for model in models:
        cfg2 = {**cfg, sec: {**cfg[sec], "model": model, **json.loads(args.set)}}
        eng = E(cfg2)
        for inp in args.inputs.split(","):
            raw = cv2.imread(inp)
            tag0 = f"{sec}_{model}_{os.path.splitext(os.path.basename(inp))[0]}" + ("_prev" if args.use_previous else "") + ("_ev" if args.event else "")
            outs, times, costs, errs = [], [], [], []
            prev = None
            if args.parallel > 1:
                def one(_):
                    t = time.time()
                    img, c, u = eng.edit(raw, eng.build_prompt(ev), None)
                    return img, time.time() - t, c
                with cf.ThreadPoolExecutor(args.parallel) as ex:
                    for res in ex.map(lambda i: _safe(one, i), range(args.repeats)):
                        if isinstance(res, Exception):
                            errs.append(str(res)[:200]); continue
                        outs.append(res[0]); times.append(res[1]); costs.append(res[2])
            else:
                for i in range(args.repeats):
                    t = time.time()
                    try:
                        img, c, u = eng.edit(raw, eng.build_prompt(ev, with_previous=prev is not None), prev)
                    except Exception as e:
                        errs.append(str(e)[:200]); print("ERR", e, flush=True); continue
                    times.append(time.time() - t); costs.append(c); outs.append(img)
                    if args.use_previous:
                        prev = img
            for i, o in enumerate(outs):
                cv2.imwrite(os.path.join(args.out, f"{tag0}_{i}.jpg"), o, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if outs:
                side = cv2.resize(raw, (outs[0].shape[1], outs[0].shape[0]))
                cv2.imwrite(os.path.join(args.out, f"sbs_{tag0}.jpg"), cv2.hconcat([side, outs[0]]), [cv2.IMWRITE_JPEG_QUALITY, 88])
            drift = [float(np.abs(small(a) - small(b)).mean()) for a, b in zip(outs, outs[1:])]
            rec = {"engine": sec, "model": model, "input": inp, "n_ok": len(outs), "errors": errs[:2],
                   "s_median": round(float(np.median(times)), 2) if times else None,
                   "s_min": round(min(times), 2) if times else None, "s_max": round(max(times), 2) if times else None,
                   "usd_per_frame": round(float(np.mean([c for c in costs if c is not None])), 4) if any(c is not None for c in costs) else None,
                   "drift_mean": round(float(np.mean(drift)), 4) if drift else None,
                   "out_size": list(outs[0].shape[:2]) if outs else None,
                   "use_previous": args.use_previous, "parallel": args.parallel, "set": json.loads(args.set)}
            print(json.dumps(rec), flush=True)
            summary.append(rec)
    with open(os.path.join(args.out, "results.jsonl"), "a") as f:
        for r in summary:
            f.write(json.dumps(r) + "\n")


def _safe(fn, i):
    try:
        return fn(i)
    except Exception as e:
        return e


if __name__ == "__main__":
    main()
