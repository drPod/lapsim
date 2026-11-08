from __future__ import annotations

from ..post import endoscope_grade


class Passthrough:
    """No model: endoscope grading + events only. Useful to test transport."""
    max_batch = 1

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def process(self, frames, events):
        out = []
        for f in frames:
            img, _ = events.paint_guide(f.img)
            img = endoscope_grade(img, self.cfg.get("post", {}))
            out.append(events.post_composite(img))
        return out
