"""Frame containers and a latest-frame hub shared by sources, the engine and outputs."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class Frame:
    seq: int
    img: np.ndarray            # BGR uint8
    t_cap: float               # capture time (source clock, seconds since epoch)
    t_recv: float              # arrival time on the GPU host
    jpeg: bytes | None = None  # original JPEG if the source sent one
    meta: dict = field(default_factory=dict)


def encode_jpeg(img: np.ndarray, quality: int = 85) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return buf.tobytes()


def decode_jpeg(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("bad jpeg")
    return img


class FrameHub:
    """Holds the most recent item and wakes waiters; also keeps a short history."""

    def __init__(self, history: int = 8):
        self._cond = threading.Condition()
        self._latest = None
        self._version = 0
        self._history: list = []
        self._max_hist = history

    def publish(self, item) -> None:
        with self._cond:
            self._latest = item
            self._version += 1
            self._history.append(item)
            if len(self._history) > self._max_hist:
                self._history.pop(0)
            self._cond.notify_all()

    def latest(self):
        with self._cond:
            return self._latest, self._version

    def since(self, version: int):
        """Items published after `version` (bounded by history length)."""
        with self._cond:
            n = self._version - version
            if n <= 0:
                return [], self._version
            return list(self._history[-min(n, len(self._history)):]), self._version

    def wait(self, version: int, timeout: float = 1.0):
        end = time.time() + timeout
        with self._cond:
            while self._version == version:
                left = end - time.time()
                if left <= 0:
                    break
                self._cond.wait(left)
            return self._latest, self._version


class RateMeter:
    def __init__(self, window: float = 2.0):
        self.window = window
        self.ts: list[float] = []

    def tick(self, t: float | None = None) -> None:
        t = time.time() if t is None else t
        self.ts.append(t)
        cut = t - self.window
        while self.ts and self.ts[0] < cut:
            self.ts.pop(0)

    @property
    def fps(self) -> float:
        if len(self.ts) < 2:
            return 0.0
        span = self.ts[-1] - self.ts[0]
        return (len(self.ts) - 1) / span if span > 0 else 0.0
