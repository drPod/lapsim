from __future__ import annotations

import glob
import os

import cv2
import httpx
import numpy as np


class HttpFrames:
    """Newest JPEG from the first URL that answers: live-restyle's raw ingest frame, else the Pi
    boxtracker's /snapshot.jpg. Nobody but the current owner touches the camera."""

    def __init__(self, urls: list[str], timeout_s: float = 2.0):
        self.urls, self.http = list(urls), httpx.Client(timeout=timeout_s)
        self.current: str | None = None
        self.last: dict[str, tuple[bytes, int]] = {}  # url -> (bytes, repeat count)

    def get(self) -> tuple[np.ndarray | None, bytes | None]:
        for url in ([self.current] if self.current else []) + [u for u in self.urls if u != self.current]:
            try:
                r = self.http.get(url)
                r.raise_for_status()
            except httpx.HTTPError:
                continue
            prev, n = self.last.get(url, (b"", 0))
            n = n + 1 if r.content == prev else 0
            self.last[url] = (r.content, n)
            if n >= 3:  # identical bytes 3x in a row: a stale frame from a stopped source
                continue
            img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                self.current = url
                return img, r.content
        self.current = None
        return None, None


class ReplayFrames:
    def __init__(self, folder: str, loop: bool = False):
        self.files = sorted(glob.glob(os.path.join(folder, "*.jpg")) + glob.glob(os.path.join(folder, "*.png")))
        self.i, self.loop = 0, loop

    def get(self):
        if self.i >= len(self.files):
            if not self.loop or not self.files:
                return None, None
            self.i = 0
        p = self.files[self.i]
        self.i += 1
        data = open(p, "rb").read()
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR), data
