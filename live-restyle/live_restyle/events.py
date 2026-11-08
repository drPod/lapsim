"""Live scenario events (bleed, smoke, bile_leak) that alter the restyle.

An event changes the output in two ways:
  * prompt: extra positive/negative text from config (``events.<type>.prompt``),
    weighted by severity, so the diffusion pass renders it as tissue/fluid;
  * conditioning: a procedural layer painted into the guide image *before*
    diffusion (blood pool, bile) or composited after it (smoke haze), plus a
    mask the engine can use to raise denoise strength locally.

Events are posted as JSON, e.g. ``{"type": "bleed", "severity": 0.7}``.
Optional keys: ``x``/``y`` (source point, 0..1 image coords), ``duration``
(seconds, then fades out), ``id`` (to update/stop a specific event).
``{"type": "clear"}`` removes everything; ``severity: 0`` stops an event.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from dataclasses import dataclass, field

import cv2
import numpy as np

EVENT_TYPES = ("bleed", "smoke", "bile_leak")


@dataclass
class Event:
    type: str
    severity: float
    x: float
    y: float
    t0: float
    duration: float | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    seed: int = field(default_factory=lambda: int(time.time() * 1000) % 100000)
    t_stop: float | None = None

    def level(self, now: float, fade: float = 2.0) -> float:
        """Severity scaled by onset ramp and fade-out (0..1)."""
        age = now - self.t0
        onset = min(1.0, age / 1.5)
        end = self.t_stop
        if self.duration is not None:
            end = self.t0 + self.duration if end is None else min(end, self.t0 + self.duration)
        out = 1.0
        if end is not None and now > end:
            out = max(0.0, 1.0 - (now - end) / fade)
        return self.severity * onset * out

    def to_dict(self, now: float) -> dict:
        return {"id": self.id, "type": self.type, "severity": self.severity,
                "x": self.x, "y": self.y, "age": round(now - self.t0, 2),
                "duration": self.duration, "level": round(self.level(now), 3)}


def _noise(h: int, w: int, seed: int, scale: int) -> np.ndarray:
    """Smooth value noise in [0,1]."""
    rng = np.random.default_rng(seed)
    small = rng.random((max(2, h // scale), max(2, w // scale))).astype(np.float32)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC).clip(0, 1)


class EventState:
    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self._lock = threading.Lock()
        self.events: dict[str, Event] = {}
        self.version = 0

    # ------------------------------------------------------------------ API
    def apply(self, msg: dict) -> dict:
        typ = msg.get("type")
        now = time.time()
        with self._lock:
            if typ == "clear":
                self.events.clear()
            elif typ in EVENT_TYPES:
                sev = float(msg.get("severity", 0.6))
                defaults = self.cfg.get(typ, {})
                eid = msg.get("id")
                existing = self.events.get(eid) if eid else next(
                    (e for e in self.events.values() if e.type == typ and e.t_stop is None), None)
                if sev <= 0:
                    if existing:
                        existing.t_stop = now
                elif existing:
                    existing.severity = min(1.0, sev)
                    existing.x = float(msg.get("x", existing.x))
                    existing.y = float(msg.get("y", existing.y))
                    if "duration" in msg:
                        existing.duration = msg["duration"]
                else:
                    ev = Event(type=typ, severity=min(1.0, sev),
                               x=float(msg.get("x", defaults.get("x", 0.5))),
                               y=float(msg.get("y", defaults.get("y", 0.6))),
                               t0=now, duration=msg.get("duration"))
                    if eid:
                        ev.id = eid
                    self.events[ev.id] = ev
            else:
                raise ValueError(f"unknown event type {typ!r}; expected one of {EVENT_TYPES + ('clear',)}")
            self._gc(now)
            self.version += 1
            return self.snapshot(now)

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        return {"events": [e.to_dict(now) for e in self.events.values()]}

    def _gc(self, now: float) -> None:
        for k in [k for k, e in self.events.items() if e.level(now) <= 0 and now - e.t0 > 2]:
            del self.events[k]

    def active(self, now: float | None = None) -> list[tuple[Event, float]]:
        now = time.time() if now is None else now
        with self._lock:
            self._gc(now)
            return [(e, e.level(now)) for e in self.events.values() if e.level(now) > 0.01]

    # ------------------------------------------------------------ prompting
    def prompt_terms(self, now: float | None = None) -> tuple[str, str]:
        pos, neg = [], []
        for ev, lvl in self.active(now):
            c = self.cfg.get(ev.type, {})
            if lvl > 0.05 and c.get("prompt"):
                pos.append(c["prompt"] if lvl > 0.4 else c.get("prompt_light", c["prompt"]))
            if c.get("negative_remove"):
                neg.append(c["negative_remove"])
        return ", ".join(pos), ", ".join(neg)

    # ------------------------------------------------------- conditioning
    def fluid_layers(self, h: int, w: int, now: float | None = None) -> list[tuple[np.ndarray, tuple, float, int]]:
        """[(mask float32 HxW 0..1, colour BGR, opacity, seed)] for blood/bile events."""
        now = time.time() if now is None else now
        out = []
        for ev, lvl in self.active(now):
            if ev.type not in ("bleed", "bile_leak"):
                continue
            c = self.cfg.get(ev.type, {})
            col = tuple(c.get("color_bgr", (18, 12, 120) if ev.type == "bleed" else (40, 150, 160)))
            out.append((self._fluid_mask(ev, lvl, now, h, w), col, float(c.get("opacity", 0.9)), ev.seed))
        return out

    def paint_guide(self, img: np.ndarray, now: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Paint fluid events into the guide image (BGR uint8, pre-diffusion).

        Returns (painted image, float mask 0..1 of changed area)."""
        h, w = img.shape[:2]
        out = img.astype(np.float32)
        mask_total = np.zeros((h, w), np.float32)
        lum = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)[..., None] / 255.0
        for m, col, opacity, seed in self.fluid_layers(h, w, now):
            # keep some underlying luminance so the diffusion pass sees shape
            fluid = np.array(col, np.float32)[None, None, :] * (0.55 + 0.6 * lum)
            spec = (_noise(h, w, seed + 7, 6) > 0.93).astype(np.float32) * m
            a = (m * opacity)[..., None]
            out = out * (1 - a) + fluid * a + spec[..., None] * 90
            mask_total = np.maximum(mask_total, m)
        return out.clip(0, 255).astype(np.uint8), mask_total

    def _fluid_mask(self, ev: Event, lvl: float, now: float, h: int, w: int) -> np.ndarray:
        c = self.cfg.get(ev.type, {})
        age = now - ev.t0
        grow = float(c.get("growth", 0.12))
        # pool radius grows ~sqrt(time) up to a severity-dependent maximum
        rmax = float(c.get("max_radius", 0.22)) * (0.35 + 0.65 * lvl)
        r = min(rmax, grow * math.sqrt(max(age, 0.05)) * (0.5 + lvl)) * min(h, w)
        cx, cy = ev.x * w, ev.y * h
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        # gravity: pool elongates downward in the image (towards the camera bottom)
        sag = float(c.get("sag", 0.6))
        dy = (yy - cy)
        dy = np.where(dy > 0, dy / (1 + sag), dy * 1.4)
        d = np.sqrt((xx - cx) ** 2 + dy ** 2)
        n = _noise(h, w, ev.seed, 24) * 0.6 + _noise(h, w, ev.seed + 1, 8) * 0.4
        edge = r * (0.65 + 0.7 * n)
        m = np.clip((edge - d) / (0.08 * r + 2), 0, 1)
        # trickles running down from the pool
        if ev.type == "bleed" and lvl > 0.3:
            rng = np.random.default_rng(ev.seed)
            for _ in range(int(2 + 4 * lvl)):
                x0 = cx + rng.uniform(-0.6, 0.6) * r
                length = r * rng.uniform(0.8, 2.2) * min(1.0, age / 4)
                width = max(2.0, r * rng.uniform(0.06, 0.14))
                wob = np.sin((yy - cy) / (8 + rng.uniform(0, 10)) + rng.uniform(0, 6)) * width
                inside = (yy > cy) & (yy < cy + r * 0.5 + length)
                t = np.clip((width - np.abs(xx - x0 - wob)) / 2, 0, 1) * inside
                m = np.maximum(m, t.astype(np.float32))
        # pulsatile spurt for severe bleeds (arterial)
        if ev.type == "bleed" and lvl > 0.6:
            phase = (now * float(c.get("pulse_hz", 1.2))) % 1.0
            if phase < 0.35:
                ang = math.radians(c.get("spurt_angle_deg", -60))
                L = r * (1.2 + 1.5 * lvl) * (phase / 0.35)
                for k in range(14):
                    s = k / 13
                    px = cx + math.cos(ang) * L * s
                    py = cy + math.sin(ang) * L * s + (L * 0.4) * s * s
                    cv2.circle(m, (int(px), int(py)), max(2, int(r * 0.05 * (1 - 0.5 * s))), 1.0, -1, cv2.LINE_AA)
        return cv2.GaussianBlur(m, (0, 0), 1.5)

    def post_composite(self, img: np.ndarray, now: float | None = None) -> np.ndarray:
        """Effects that look best composited after diffusion (smoke haze)."""
        now = time.time() if now is None else now
        smoke = [(e, l) for e, l in self.active(now) if e.type == "smoke"]
        if not smoke:
            return img
        h, w = img.shape[:2]
        out = img.astype(np.float32)
        for ev, lvl in smoke:
            c = self.cfg.get("smoke", {})
            t = now - ev.t0
            # drifting, slowly evolving haze: two noise octaves scrolled over time
            sx, sy = int(t * 9) % w, int(t * 5) % h
            n1 = np.roll(_noise(h, w, ev.seed, 48), (sy, sx), (0, 1))
            n2 = np.roll(_noise(h, w, ev.seed + 3, 16), (-sy // 2, sx // 3), (0, 1))
            dens = (0.55 * n1 + 0.45 * n2)
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            src = np.exp(-(((xx / w - ev.x) ** 2) + ((yy / h - ev.y) ** 2)) / 0.08)
            a = np.clip(lvl * float(c.get("density", 0.75)) * (0.35 + 0.65 * dens) * (0.5 + 0.5 * src), 0, 0.92)
            haze = np.array(c.get("color_bgr", (200, 205, 210)), np.float32)
            blurred = cv2.GaussianBlur(out, (0, 0), 3 + 6 * lvl)
            out = blurred * (a[..., None] * 0.6) + out * (1 - a[..., None] * 0.6)
            out = out * (1 - a[..., None]) + haze * a[..., None]
        return out.clip(0, 255).astype(np.uint8)
