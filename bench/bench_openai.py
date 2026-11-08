#!/usr/bin/env python3
"""Benchmark OpenAI image-edit models on stills: seconds and USD per frame.

python bench/bench_openai.py --models gpt-image-2.5-flare,gpt-image-2.6-flare --qualities medium,high \
    --inputs refs/raw_real_scene.png --out runs/openai
"""
import argparse
import json
import os
import sys
import time

import cv2
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_restyle.engines.openai_edit import OpenAIEditEngine  # noqa: E402
from live_restyle.events import EventState  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/restyle.yaml")
    ap.add_argument("--models", default=None)
    ap.add_argument("--qualities", default=None)
    ap.add_argument("--inputs", default="refs/raw_real_scene.png")
    ap.add_argument("--event", default=None, help='e.g. {"type":"bleed","severity":0.7}')
    ap.add_argument("--extra", default="{}", help="extra request params as JSON, e.g. {\"complexity\":\"low\"}")
    ap.add_argument("--out", default="runs/openai")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config))
    os.makedirs(args.out, exist_ok=True)
    ev = EventState(cfg.get("events", {}))
    if args.event:
        ev.apply(json.loads(args.event))
        for e in ev.events.values():
            e.t0 -= 10  # fully ramped
    eng = OpenAIEditEngine(cfg)
    models = (args.models or cfg["openai"]["model"]).split(",")
    quals = (args.qualities or cfg["openai"]["quality"]).split(",")
    results = []
    for model in models:
        for q in quals:
            for inp in args.inputs.split(","):
                eng.cfg = {**cfg, "openai": {**cfg["openai"], "model": model, "quality": q, **json.loads(args.extra)}}
                prompt = eng.build_prompt(ev)
                raw = cv2.imread(inp)
                t = time.time()
                try:
                    img, usage = eng.edit(raw, prompt)
                except Exception as e:
                    print(model, q, "ERROR", e)
                    results.append({"model": model, "quality": q, "input": inp, "error": str(e)[:300]})
                    continue
                dt = time.time() - t
                tag = f"{model}_{q}_{os.path.splitext(os.path.basename(inp))[0]}" + ("_ev" if args.event else "")
                cv2.imwrite(os.path.join(args.out, tag + ".jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
                side = cv2.resize(raw, (img.shape[1], img.shape[0]))
                cv2.imwrite(os.path.join(args.out, "sbs_" + tag + ".jpg"), cv2.hconcat([side, img]),
                            [cv2.IMWRITE_JPEG_QUALITY, 88])
                rec = {"model": model, "quality": q, "input": inp, "seconds": round(dt, 1),
                       "cost_usd": round(eng.cost(usage) or 0, 4), "usage": usage, "out": tag + ".jpg"}
                print(json.dumps({k: v for k, v in rec.items() if k != "usage"}), flush=True)
                results.append(rec)
    with open(os.path.join(args.out, "results.json"), "a") as f:
        f.write(json.dumps(results) + "\n")


if __name__ == "__main__":
    main()
