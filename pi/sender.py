#!/usr/bin/env python3
"""Pi camera → GPU: push JPEG frames over WebSocket to live-restyle's /ingest.

Message format: float64 little-endian capture time (unix seconds) + JPEG bytes.
Only the newest frame is sent; if the link is slow, frames are dropped, not queued.

Usage (on the Pi; stop the boxtracker service first, it owns the camera):
    systemctl --user stop boxtracker
    python3 sender.py --url ws://100.75.165.4:8090/ingest --size 1280x720 --fps 30
    systemctl --user start boxtracker
"""
import argparse
import asyncio
import struct
import threading
import time

import simplejpeg
import websockets
from picamera2 import Picamera2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://100.75.165.4:8090/ingest")
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--quality", type=int, default=85)
    ap.add_argument("--af", default="continuous", choices=["continuous", "auto", "manual"])
    ap.add_argument("--lens", type=float, default=None, help="manual focus lens position (dioptres)")
    ap.add_argument("--hflip", action="store_true")
    ap.add_argument("--vflip", action="store_true")
    args = ap.parse_args()
    w, h = map(int, args.size.split("x"))

    from libcamera import Transform, controls
    cam = Picamera2()
    cfg = cam.create_video_configuration(main={"size": (w, h), "format": "RGB888"},
                                         transform=Transform(hflip=args.hflip, vflip=args.vflip),
                                         controls={"FrameRate": args.fps}, buffer_count=4)
    cam.configure(cfg)
    cam.start()
    try:
        if args.af == "manual" and args.lens is not None:
            cam.set_controls({"AfMode": controls.AfModeEnum.Manual, "LensPosition": args.lens})
        elif args.af == "continuous":
            cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
    except Exception as e:  # not all sensors have AF
        print("AF control failed:", e)

    latest = {"data": None, "n": 0}
    cond = threading.Condition()

    def capture():
        while True:
            arr = cam.capture_array("main")  # RGB888 in picamera2 is BGR byte order
            t = time.time()
            jpg = simplejpeg.encode_jpeg(arr, quality=args.quality, colorspace="BGR", fastdct=True)
            with cond:
                latest["data"] = struct.pack("<d", t) + jpg
                latest["n"] += 1
                cond.notify_all()

    threading.Thread(target=capture, daemon=True).start()

    async def run():
        while True:
            try:
                async with websockets.connect(args.url, max_size=None, ping_interval=5) as ws:
                    print("connected", args.url)
                    seen, sent, t0 = 0, 0, time.time()
                    while True:
                        def wait():
                            with cond:
                                cond.wait_for(lambda: latest["n"] != seen, timeout=1)
                                return latest["n"], latest["data"]
                        seen, data = await asyncio.to_thread(wait)
                        if data is None:
                            continue
                        await ws.send(data)
                        sent += 1
                        if time.time() - t0 > 5:
                            print(f"{sent / (time.time() - t0):.1f} fps, {len(data) // 1024} KB/frame")
                            sent, t0 = 0, time.time()
            except Exception as e:
                print("connection error:", e, "- retrying")
                await asyncio.sleep(1)

    asyncio.run(run())


if __name__ == "__main__":
    main()
