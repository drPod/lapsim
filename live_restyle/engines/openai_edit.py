"""OpenAI Images edit backend: POST /v1/images/edits with image[] = (camera frame, style target[, previous output]).

Needs OPENAI_API_KEY. Never logged.
"""
from __future__ import annotations

import base64
import os

import cv2
import httpx
import numpy as np

from ..frames import encode_jpeg
from .slow_edit import SlowEditEngine

# USD per 1M tokens. GPT Image 2.5 rates from OpenAI's announcement; 2.6 assumed equal
# until published. Override in config `openai.pricing`.
DEFAULT_PRICING = {"text_in": 5.0, "image_in": 8.0, "image_out": 30.0, "text_out": 10.0}


class OpenAIEditEngine(SlowEditEngine):
    section = "openai"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        self.client = httpx.Client(base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                                   headers={"Authorization": f"Bearer {key}"}, timeout=300)

    def edit(self, frame_bgr, prompt, prev_bgr):
        o = self.o
        data = {"model": o.get("model", "gpt-image-2.5-flare"), "prompt": prompt, "n": "1",
                "size": o.get("size", "1536x864"), "quality": o.get("quality", "high"),
                "output_format": "jpeg", "output_compression": str(o.get("output_compression", 92))}
        for k in ("input_fidelity", "complexity", "moderation"):
            if o.get(k):
                data[k] = o[k]
        files = [("image[]", ("frame.jpg", encode_jpeg(self.fit(frame_bgr), 95), "image/jpeg")),
                 ("image[]", ("style.jpg", self.reference_jpeg(), "image/jpeg"))]
        if prev_bgr is not None:
            files.append(("image[]", ("previous.jpg", encode_jpeg(self.fit(prev_bgr), 90), "image/jpeg")))
        r = self.client.post("/images/edits", data=data, files=files)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
        j = r.json()
        img = cv2.imdecode(np.frombuffer(base64.b64decode(j["data"][0]["b64_json"]), np.uint8), cv2.IMREAD_COLOR)
        usage = j.get("usage", {})
        return img, self.cost(usage), usage

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
        return (txt_in * p["text_in"] + img_in * p["image_in"] + img_out * p["image_out"] + txt_out * p["text_out"]) / 1e6
