"""Pipe output frames into ffmpeg → RTSP (MediaMTX re-serves as WebRTC/WHEP, HLS, RTSP)."""
from __future__ import annotations

import logging
import subprocess
import threading
import time

import cv2

from .frames import FrameHub

log = logging.getLogger("live_restyle.publish")


class RtspPublisher(threading.Thread):
    def __init__(self, hub: FrameHub, url: str, cfg: dict):
        super().__init__(daemon=True, name="rtsp")
        self.hub, self.url = hub, url
        self.fps = int(cfg.get("rtsp_fps", 15))
        self.bitrate = cfg.get("rtsp_bitrate", "4M")

    def _start(self, w: int, h: int) -> subprocess.Popen:
        cmd = ["ffmpeg", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{w}x{h}", "-r", str(self.fps), "-i", "-",
               "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
               "-profile:v", "baseline", "-pix_fmt", "yuv420p", "-g", str(self.fps),
               "-bf", "0", "-b:v", self.bitrate, "-f", "rtsp", "-rtsp_transport", "tcp", self.url]
        log.info("rtsp publish → %s", self.url)
        return subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def run(self) -> None:
        # Constant-rate writer: repeats the latest frame so the encoder clock stays steady.
        proc, size = None, None
        period = 1.0 / self.fps
        nxt = time.time()
        while True:
            f, _ = self.hub.latest()
            if f is not None:
                img = f.img
                if img.shape[1] % 2 or img.shape[0] % 2:
                    img = cv2.resize(img, (img.shape[1] // 2 * 2, img.shape[0] // 2 * 2))
                if proc is None or proc.poll() is not None or size != img.shape[:2]:
                    if proc is not None:
                        proc.kill()
                        time.sleep(1)
                    size = img.shape[:2]
                    proc = self._start(img.shape[1], img.shape[0])
                try:
                    proc.stdin.write(img.tobytes())
                except (BrokenPipeError, OSError):
                    proc = None
            nxt += period
            time.sleep(max(0.0, nxt - time.time()))
            if time.time() - nxt > 1:
                nxt = time.time()
