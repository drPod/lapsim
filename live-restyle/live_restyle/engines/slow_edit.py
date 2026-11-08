"""Shared loop for slow, API-based two-image edit engines (Gemini, OpenAI).

`concurrency` workers run staggered: each grabs the newest camera frame, sends
(frame = structure, approved target = style[, previous output = continuity]) plus the
instruction (with event sentences), and hands the result to `svc.present`, which holds
the last good frame, crossfades to the new one and drops results older than what is shown.
"""
from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np

from ..frames import encode_jpeg

log = logging.getLogger("live_restyle.slow")


def _where(x: float, y: float) -> str:
    v = "upper" if y < 0.4 else "lower" if y > 0.6 else "middle"
    h = "left" if x < 0.4 else "right" if x > 0.6 else "centre"
    return "the centre of the image" if (v, h) == ("middle", "centre") else f"the {v} {h} of the image"


class SlowEditEngine:
    max_batch = 1
    section = "slow"           # config section name

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.stats = {"requests": 0, "ok": 0, "errors": 0, "last_s": None, "avg_s": None,
                      "last_cost_usd": None, "total_cost_usd": 0.0, "last_error": None, "in_flight": 0,
                      "last_drift": None, "avg_drift": None}
        self._lock = threading.Lock()
        self._ref = (None, None)          # (path, jpeg bytes)
        self.prev_out = None              # last presented output (BGR) for continuity reference
        self._last_present = None

    def update_config(self, cfg: dict):
        self.cfg = cfg

    @property
    def o(self) -> dict:
        return self.cfg.get(self.section, {})

    # ------------------------------------------------------------- inputs
    def fit(self, img: np.ndarray) -> np.ndarray:
        m = int(self.o.get("input_max_side", 1280))
        h, w = img.shape[:2]
        s = m / max(h, w)
        return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img

    def reference_jpeg(self) -> bytes:
        path = self.o.get("reference", "refs/target_style.png")
        if self._ref[0] != path:
            img = cv2.imread(path)
            if img is None:
                raise FileNotFoundError(path)
            self._ref = (path, encode_jpeg(self.fit(img), 92))
        return self._ref[1]

    def build_prompt(self, events, with_previous: bool = False) -> str:
        p = self.o.get("prompt", "").strip()
        if with_previous and self.o.get("previous_prompt"):
            p += " " + self.o["previous_prompt"].strip()
        sentences = []
        for ev, lvl in events.active():
            s = self.cfg.get("events", {}).get(ev.type, {}).get("instruction")
            if s:
                sev = "severe" if lvl > 0.66 else "moderate" if lvl > 0.33 else "mild"
                sentences.append(s.format(severity=sev, where=f" near {_where(ev.x, ev.y)}").strip())
        return p + ("" if not sentences else " " + " ".join(sentences))

    # ------------------------------------------------------------- backend
    def edit(self, frame_bgr: np.ndarray, prompt: str, prev_bgr: np.ndarray | None) -> tuple[np.ndarray, float | None, dict]:
        raise NotImplementedError

    # ------------------------------------------------------------- loop
    def run(self, svc) -> None:
        n = int(self.o.get("concurrency", 1))
        for i in range(n):
            threading.Thread(target=self._worker, args=(svc, i, n), daemon=True, name=f"{self.section}{i}").start()

    def _worker(self, svc, idx: int, n: int) -> None:
        time.sleep(idx * float(self.o.get("expected_s", 3)) / n)
        while svc.running:
            if self.o.get("paused", False):
                time.sleep(0.5)
                continue
            f, _ = svc.raw.latest()
            if f is None:
                time.sleep(0.2)
                continue
            use_prev = bool(self.o.get("use_previous", False)) and self.prev_out is not None
            prev = self.prev_out if use_prev else None
            prompt = self.build_prompt(svc.events, with_previous=use_prev)
            t0 = time.time()
            with self._lock:
                self.stats["requests"] += 1
                self.stats["in_flight"] += 1
            try:
                img, cost, usage = self.edit(f.img, prompt, prev)
                dt = time.time() - t0
                drift = self._drift(img)
                with self._lock:
                    s = self.stats
                    s["ok"] += 1
                    s["last_s"] = round(dt, 2)
                    s["avg_s"] = round(dt if s["avg_s"] is None else 0.8 * s["avg_s"] + 0.2 * dt, 2)
                    if cost is not None:
                        s["last_cost_usd"] = round(cost, 4)
                        s["total_cost_usd"] = round(s["total_cost_usd"] + cost, 4)
                    if drift is not None:
                        s["last_drift"] = round(drift, 4)
                        s["avg_drift"] = round(drift if s["avg_drift"] is None else 0.8 * s["avg_drift"] + 0.2 * drift, 4)
                    s["last_usage"] = usage
                if svc.present(f, img, meta={"gen_s": round(dt, 2), "cost_usd": cost, "drift": drift}) is not False:
                    self.prev_out = img
                log.info("%s edit %.2fs cost=%s drift=%s", self.section, dt, cost, drift)
            except Exception as e:
                with self._lock:
                    self.stats["errors"] += 1
                    self.stats["last_error"] = str(e)[:300]
                log.warning("%s edit failed: %s", self.section, str(e)[:300])
                time.sleep(float(self.o.get("error_backoff_s", 3)))
            finally:
                with self._lock:
                    self.stats["in_flight"] -= 1
            gap = float(self.o.get("min_interval_s", 0))
            if gap:
                time.sleep(gap)

    def _drift(self, img: np.ndarray) -> float | None:
        """Mean abs difference (0..1) to the previous result at 128 px, a cheap flicker/drift metric.
        It also includes real scene motion, so compare it against the raw frames' own difference."""
        small = cv2.resize(img, (128, 72), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
        prev = self._last_present
        self._last_present = small
        if prev is None:
            return None
        return float(np.abs(small - prev).mean())
