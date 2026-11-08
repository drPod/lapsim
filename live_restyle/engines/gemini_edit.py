"""Google Gemini image edit backend ("Nano Banana 2" family) via REST generateContent.

Parts: [instruction, image 1 = camera frame, image 2 = approved style target(, image 3 = previous output)].
Needs GEMINI_API_KEY. Never logged.
"""
from __future__ import annotations

import base64
import os

import cv2
import httpx
import numpy as np

from ..frames import encode_jpeg
from .slow_edit import SlowEditEngine

# USD per 1M tokens (Gemini API pricing page, Nov 2026, paid tier standard)
PRICING = {
    "gemini-nano-banana-2.1": {"in": 1.50, "text_out": 7.50, "image_out": 30.0},
    "gemini-3.1-flash-image": {"in": 0.50, "text_out": 3.00, "image_out": 60.0},
    "gemini-3.1-flash-lite-image": {"in": 0.25, "text_out": 1.50, "image_out": 30.0},
    "gemini-3-pro-image": {"in": 2.00, "text_out": 12.0, "image_out": 120.0},
}


class GeminiEditEngine(SlowEditEngine):
    section = "gemini"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        base = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")
        self.client = httpx.Client(base_url=base, headers={"x-goog-api-key": key}, timeout=120,
                                   limits=httpx.Limits(max_connections=16, max_keepalive_connections=16))

    def edit(self, frame_bgr, prompt, prev_bgr):
        o = self.o
        model = o.get("model", "gemini-3.1-flash-image")
        parts = [{"text": prompt},
                 {"inline_data": {"mime_type": "image/jpeg",
                                  "data": base64.b64encode(encode_jpeg(self.fit(frame_bgr), 92)).decode()}},
                 {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(self.reference_jpeg()).decode()}}]
        if prev_bgr is not None:
            parts.append({"inline_data": {"mime_type": "image/jpeg",
                                          "data": base64.b64encode(encode_jpeg(self.fit(prev_bgr), 88)).decode()}})
        gc = {"responseModalities": ["IMAGE"],
              "imageConfig": {"aspectRatio": o.get("aspect_ratio", "16:9"), "imageSize": str(o.get("image_size", "1K"))}}
        if o.get("thinking_level"):
            gc["thinkingConfig"] = {"thinkingLevel": o["thinking_level"]}
        for k in ("temperature", "seed", "topP"):
            if o.get(k) is not None:
                gc[k] = o[k]
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gc}
        api = o.get("api_version", "v1beta")
        r = self.client.post(f"/{api}/models/{model}:generateContent", json=body)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:400]}")
        j = r.json()
        img = None
        for cand in j.get("candidates", []):
            for p in (cand.get("content") or {}).get("parts", []):
                d = p.get("inlineData") or p.get("inline_data")
                if d and not p.get("thought"):
                    img = cv2.imdecode(np.frombuffer(base64.b64decode(d["data"]), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            fr = [c.get("finishReason") for c in j.get("candidates", [])]
            raise RuntimeError(f"no image in response (finish={fr}, feedback={j.get('promptFeedback')})")
        usage = j.get("usageMetadata", {})
        return img, self.cost(model, usage), usage

    def cost(self, model: str, u: dict) -> float | None:
        p = {**PRICING.get(model, PRICING["gemini-3.1-flash-image"]), **self.o.get("pricing", {})}
        if not u:
            return None
        img_out = sum(d.get("tokenCount", 0) for d in u.get("candidatesTokensDetails", []) if d.get("modality") == "IMAGE")
        cand = u.get("candidatesTokenCount", 0)
        text_out = max(0, cand - img_out) + u.get("thoughtsTokenCount", 0)
        if not u.get("candidatesTokensDetails"):
            img_out, text_out = cand, u.get("thoughtsTokenCount", 0)
        return (u.get("promptTokenCount", 0) * p["in"] + img_out * p["image_out"] + text_out * p["text_out"]) / 1e6
