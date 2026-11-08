"""Heavy edit model on every frame (quality reference; ~1 s/frame)."""
from __future__ import annotations

import cv2

from ..keyframe import EditModel
from ..post import endoscope_grade


class EditEngine:
    max_batch = 1

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.model = EditModel(cfg["edit"])
        self.ref = cv2.imread(cfg["edit"]["reference"])

    def update_config(self, cfg: dict):
        self.cfg = cfg
        self.ref = cv2.imread(cfg["edit"]["reference"])

    def process(self, frames, events):
        e = self.cfg["edit"]
        f = frames[-1]
        img, _ = events.paint_guide(f.img)
        pos, _ = events.prompt_terms()
        prompt = e["prompt"] + (" Also: " + pos + "." if pos else "")
        out = self.model(img, self.ref, prompt, int(e.get("width", 1024)), int(e.get("height", 576)),
                         seed=int(e.get("seed", 7)))[0]
        out = endoscope_grade(out, {**self.cfg.get("post", {}), "vignette": 0.0, "bloom": 0.0}) \
            if self.cfg.get("edit", {}).get("grade", False) else out
        return [events.post_composite(out)] * len(frames[-1:])
