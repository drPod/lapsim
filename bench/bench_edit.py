#!/usr/bin/env python3
"""Benchmark two-image edit models (structure frame + style reference).

python bench/bench_edit.py --model Qwen/Qwen-Image-Edit-2511 --steps 4 --sizes 1024x576,768x432 \
    --inputs refs/raw_real_scene.png,refs/raw_real_scene2.png --out runs/edit_2511
"""
import argparse
import json
import os
import sys
import time

import cv2
import torch
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_restyle.keyframe import EditModel  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/restyle.yaml")
    ap.add_argument("--model", default=None)
    ap.add_argument("--steps", type=int, default=None)
    ap.add_argument("--no-lightning", action="store_true")
    ap.add_argument("--lightning-weight", default=None)
    ap.add_argument("--true-cfg", type=float, default=None)
    ap.add_argument("--compile", action="store_true")
    ap.add_argument("--sizes", default="1024x576")
    ap.add_argument("--inputs", default="refs/raw_real_scene.png,refs/raw_real_scene2.png")
    ap.add_argument("--reference", default=None)
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--seeds", default="7")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--out", default="runs/edit")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config))
    ecfg = dict(cfg["edit"])
    if args.model:
        ecfg["model"] = args.model
    if args.steps:
        ecfg["steps"] = args.steps
    if args.no_lightning:
        ecfg["lightning"] = False
    if args.lightning_weight:
        ecfg["lightning_weight_name"] = args.lightning_weight
    if args.true_cfg is not None:
        ecfg["true_cfg"] = args.true_cfg
    ecfg["compile"] = args.compile
    os.makedirs(args.out, exist_ok=True)
    prompt = args.prompt or ecfg["prompt"]
    ref = cv2.imread(args.reference or ecfg["reference"])
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    m = EditModel(ecfg)
    load_s = time.time() - t0
    results = {"model": ecfg["model"], "steps": ecfg["steps"], "lightning": ecfg.get("lightning", True),
               "load_s": round(load_s, 1), "runs": []}
    for size in args.sizes.split(","):
        w, h = map(int, size.split("x"))
        for inp in args.inputs.split(","):
            raw = cv2.imread(inp)
            for seed in map(int, args.seeds.split(",")):
                times = []
                for r in range(args.repeat):
                    torch.cuda.synchronize()
                    t = time.time()
                    out = m(raw, ref, prompt, w, h, seed=seed)[0]
                    torch.cuda.synchronize()
                    times.append(time.time() - t)
                name = f"{os.path.splitext(os.path.basename(inp))[0]}_{w}x{h}_s{seed}.jpg"
                cv2.imwrite(os.path.join(args.out, name), out, [cv2.IMWRITE_JPEG_QUALITY, 92])
                side = cv2.resize(raw, (out.shape[1], out.shape[0]))
                cv2.imwrite(os.path.join(args.out, "sbs_" + name), cv2.hconcat([side, out]),
                            [cv2.IMWRITE_JPEG_QUALITY, 88])
                rec = {"input": inp, "size": size, "seed": seed, "first_s": round(times[0], 3),
                       "median_s": round(sorted(times)[len(times) // 2], 3), "out": name}
                print(rec, flush=True)
                results["runs"].append(rec)
    results["peak_vram_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 1)
    print("peak VRAM GB", results["peak_vram_gb"])
    json.dump(results, open(os.path.join(args.out, "results.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
