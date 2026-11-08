"""OpenAI Images edit backend: (latest camera frame = structure, approved target = style).

Each worker grabs the newest raw frame, sends both images to POST /v1/images/edits
(multipart, `image[]` twice), and hands the result to the service, which keeps showing
the last good frame and crossfades to the new one. With `concurrency` > 1 several
requests are in flight, staggered, so results arrive more often (cost scales with it).

Needs OPENAI_API_KEY in the environment. Never logged.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import time

import cv2
import httpx
import numpy as np

from ..frames import encode_jpeg

log = logging.getLogger("live_restyle.openai")

# USD per 1M tokens. GPT Image 2.5 rates from OpenAI's announcement; 2.6 assumed equal
# until OpenAI publishes them. Override in config `openai.pricing`.
DEFAULT_PRICING = {"text_in": 5.0, "image_in": 8.0, "image_out": 30.0}


class OpenAIEditEngine:
    max_batch = 1
    crossfade = True

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.key = os.environ.get("OPENAI_API_KEY")
        if not self.key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        self.client = httpx.Client(base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                                   headers={"Authorization": f"Bearer {self.key}"}, timeout=300)
        self.stats = {"requests": 0, "ok": 0, "errors": 0, "last_s": None, "avg_s": None,
                      "last_cost_usd": None, "total_cost_usd": 0.0, "last_error": None, "in_flight": 0}
        self._lock = threading.Lock()
        self._ref_jpeg = None
        self._ref_path = None

    def update_config(self, cfg: dict):
        self.cfg = cfg

    @property
    def o(self) -> dict:
        return self.cfg.get("openai", {})

    def _reference(self) -> bytes:
        path = self.o.get("reference", self.cfg.get("edit", {}).get("reference", "refs/target_style.png"))
        if path != self._ref_path:
            img = cv2.imread(path)
            if img is None:
                raise FileNotFoundError(path)
            self._ref_jpeg = encode_jpeg(self._fit(img), 95)
            self._ref_path = path
        return self._ref_jpeg

    def _fit(self, img: np.ndarray) -> np.ndarray:
        """Downscale so the long side is at most `input_max_side` (saves input tokens/upload)."""
        m = int(self.o.get("input_max_side", 1536))
        h, w = img.shape[:2]
        s = m / max(h, w)
        return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img

    def build_prompt(self, events) -> str:
        p = self.o.get("prompt", "").strip()
        sentences = []
        for ev, lvl in events.active():
            c = self.cfg.get("events", {}).get(ev.type, {})
            s = c.get("instruction")
            if s:
                sev = "severe" if lvl > 0.66 else "moderate" if lvl > 0.33 else "mild"
                where = f" near {_where(ev.x, ev.y)}"
                sentences.append(s.format(severity=sev, where=where).strip())
        return p + ("" if not sentences else " " + " ".join(sentences))

    def edit(self, frame_bgr: np.ndarray, prompt: str) -> tuple[np.ndarray, dict]:
        o = self.o
        data = {"model": o.get("model", "gpt-image-2.5-flare"), "prompt": prompt, "n": "1",
                "size": o.get("size", "1536x864"), "quality": o.get("quality", "high"),
                "output_format": "jpeg", "output_compression": str(o.get("output_compression", 92))}
        for k in ("input_fidelity", "complexity", "moderation"):
            if o.get(k):
                data[k] = o[k]
        files = [("image[]", ("frame.jpg", encode_jpeg(self._fit(frame_bgr), 95), "image/jpeg")),
                 ("image[]", ("style.jpg", self._reference(), "image/jpeg"))]
        r = self.client.post("/images/edits", data=data, files=files)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
        j = r.json()
        img = cv2.imdecode(np.frombuffer(base64.b64decode(j["data"][0]["b64_json"]), np.uint8), cv2.IMREAD_COLOR)
        return img, j.get("usage", {})

    def cost(self, usage: dict) -> float | None:
        if not usage:
            return None
        p = {**DEFAULT_PRICING, **self.o.get("pricing", {})}
        ind = usage.get("input_tokens_details", {}) or {}
        outd = usage.get("output_tokens_details", {}) or {}
        img_in = ind.get("image_tokens", 0)
        txt_in = ind.get("text_tokens", usage.get("input_tokens", 0) - img_in)
        img_out = outd.get("image_tokens", usage.get("output_tokens", 0))
        txt_out = outd.get("text_tokens", 0)
        return (txt_in * p["text_in"] + img_in * p["image_in"] + img_out * p["image_out"]
                + txt_out * p.get("text_out", 10.0)) / 1e6

    # ---------------------------------------------------------------- loop
    def run(self, svc) -> None:
        n = int(self.o.get("concurrency", 1))
        for i in range(n):
            threading.Thread(target=self._worker, args=(svc, i, n), daemon=True, name=f"openai{i}").start()

    def _worker(self, svc, idx: int, n: int) -> None:
        # stagger the workers across one expected request time
        time.sleep(idx * float(self.o.get("expected_s", 20)) / n)
        last_seq = -1
        while svc.running:
            if self.o.get("paused", False):
                time.sleep(1)
                continue
            f, _ = svc.raw.latest()
            if f is None or f.seq == last_seq and not self.o.get("repeat_same_frame", True):
                time.sleep(0.2)
                continue
            last_seq = f.seq
            prompt = self.build_prompt(svc.events)
            t0 = time.time()
            with self._lock:
                self.stats["requests"] += 1
                self.stats["in_flight"] += 1
            try:
                img, usage = self.edit(f.img, prompt)
                dt = time.time() - t0
                c = self.cost(usage)
                with self._lock:
                    s = self.stats
                    s["ok"] += 1
                    s["last_s"] = round(dt, 1)
                    s["avg_s"] = round(dt if s["avg_s"] is None else 0.7 * s["avg_s"] + 0.3 * dt, 1)
                    if c is not None:
                        s["last_cost_usd"] = round(c, 4)
                        s["total_cost_usd"] = round(s["total_cost_usd"] + c, 4)
                    s["last_usage"] = usage
                log.info("openai edit %.1fs cost=%s prompt_events=%s", dt, c, prompt != self.o.get("prompt", "").strip())
                svc.present(f, img, meta={"gen_s": round(dt, 2), "cost_usd": c})
            except Exception as e:
                with self._lock:
                    self.stats["errors"] += 1
                    self.stats["last_error"] = str(e)[:300]
                log.warning("openai edit failed: %s", str(e)[:300])
                time.sleep(5)
            finally:
                with self._lock:
                    self.stats["in_flight"] -= 1
            gap = float(self.o.get("min_interval_s", 0))
            if gap:
                time.sleep(gap)


def _where(x: float, y: float) -> str:
    v = "upper" if y < 0.4 else "lower" if y > 0.6 else "middle"
    h = "left" if x < 0.4 else "right" if x > 0.6 else "centre"
    return "the centre of the image" if (v, h) == ("middle", "centre") else f"the {v} {h} of the image"
