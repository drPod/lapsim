"""Deterministic post-processing: endoscope look (vignette, grade, glare bloom)."""
from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np


@lru_cache(maxsize=8)
def _vignette(h: int, w: int, strength: float, radius: float, softness: float) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # elliptical falloff that is slightly rounder than the frame, like a scope image
    nx = (xx - w / 2) / (w / 2)
    ny = (yy - h / 2) / (h / 2) * 0.85
    r = np.sqrt(nx ** 2 + ny ** 2)
    v = 1.0 - strength * np.clip((r - radius) / softness, 0, 1) ** 1.6
    return v[..., None].astype(np.float32)


def endoscope_grade(img: np.ndarray, cfg: dict | None = None) -> np.ndarray:
    cfg = cfg or {}
    if not cfg.get("enabled", True):
        return img
    h, w = img.shape[:2]
    x = img.astype(np.float32) / 255.0
    # contrast curve + slight warm/red bias typical of xenon/LED scopes on tissue
    gamma = float(cfg.get("gamma", 1.1))
    x = np.power(x, gamma)
    gain = np.array(cfg.get("gain_bgr", (0.96, 0.98, 1.04)), np.float32)
    x = x * gain
    # glare bloom on specular highlights
    bloom = float(cfg.get("bloom", 0.25))
    if bloom > 0:
        hi = np.clip(x.max(axis=2, keepdims=True) - 0.8, 0, 1) * 5
        glow = cv2.GaussianBlur((x * hi).astype(np.float32), (0, 0), max(2, w / 200))
        x = x + bloom * glow.reshape(x.shape)
    v = _vignette(h, w, float(cfg.get("vignette", 0.85)), float(cfg.get("vignette_radius", 0.55)),
                  float(cfg.get("vignette_softness", 0.65)))
    x = x * v
    return (np.clip(x, 0, 1) * 255).astype(np.uint8)
