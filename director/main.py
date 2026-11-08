"""Main loop: newest frame -> CV pre-check -> Observer (VLM, JSON) -> rules -> Director (VLM, tool calls)
-> VR + live-restyle events. Everything is logged as JSONL under logs/<run>/ for the debrief."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path

import cv2

from . import config as config_mod
from .cvcheck import analyse
from .engine import Engine
from .frames import HttpFrames, ReplayFrames
from .sinks import JsonlLog, RestyleSink, VRSink
from .vlm import VLM

log = logging.getLogger("director")


class Director:
    def __init__(self, cfg: dict, dry_run: bool = False):
        self.cfg = cfg
        run = time.strftime("%Y%m%d-%H%M%S")
        self.dir = Path(cfg.get("log_dir", "logs")) / run
        (self.dir / "frames").mkdir(parents=True, exist_ok=True)
        self.log = JsonlLog(self.dir / "events.jsonl")
        f = cfg["frames"]
        self.frames = ReplayFrames(f["replay_dir"]) if f.get("replay_dir") else HttpFrames(f["urls"], f.get("timeout_s", 2))
        self.vlm = VLM(cfg["vlm"])
        self.engine = Engine(cfg)
        s = cfg["sinks"]
        self.sinks = []
        if not dry_run:
            if s["vr"].get("enabled"):
                self.sinks.append(("vr", VRSink(s["vr"])))
            if s["restyle"].get("enabled"):
                self.sinks.append(("restyle", RestyleSink(s["restyle"])))
        self.director_task: asyncio.Task | None = None
        self.last_vitals = 0.0
        self.tick_n = 0
        self.stop = asyncio.Event()

    async def emit(self, events: list[dict], call: dict):
        for ev in events:
            res = {}
            for name, sink in self.sinks:
                res[name] = await sink.send(ev)
            if ev["type"] != "vitals":
                self.log.write("event", event=ev, via=res, call=call["name"], source=call.get("source"))
                log.info("EVENT %-18s %s %s", ev["type"], json.dumps({k: v for k, v in ev.items() if k != "type"}), res)

    async def run_calls(self, calls: list[dict], now: float):
        done = []
        for c in calls:
            why = self.engine.validate(now, c)
            self.log.write("tool_call", name=c["name"], source=c.get("source"), args=c.get("arguments"),
                           accepted=why is None, rejected=why)
            if why is not None:
                if c.get("source") != "rule" or why not in ("cooldown",):
                    log.debug("reject %s (%s): %s", c["name"], c.get("source"), why)
                continue
            log.info("CALL  %-18s [%s] %s", c["name"], c.get("source"), json.dumps(c.get("arguments")))
            await self.emit(self.engine.execute(now, c), c)
            done.append(c)
        return done

    async def direct(self, ctx: dict):
        try:
            calls, meta = await asyncio.to_thread(self.vlm.direct, ctx)
        except Exception as e:  # noqa: BLE001
            self.log.write("director_error", error=repr(e))
            return
        for c in calls:
            c["source"] = "director"
        self.log.write("director", calls=calls, **meta)
        await self.run_calls(calls, time.time())

    async def tick(self, dt: float):
        now = time.time()
        img, raw = await asyncio.to_thread(self.frames.get)
        if img is None:
            self.log.write("frame_error")
            return False
        self.tick_n += 1
        fname = f"{self.tick_n:05d}.jpg"
        (self.dir / "frames" / fname).write_bytes(raw)
        cv = analyse(img, contact_px=self.cfg["contact"]["cv_contact_px"]).to_dict()
        obs = None
        if self.cfg["contact"]["fusion"] != "cv":
            try:
                obs = await asyncio.to_thread(self.vlm.observe, img)
            except Exception as e:  # noqa: BLE001
                obs = {"error": repr(e)}
        fused = self.engine.fuse(now, cv, obs)
        self.log.write("observation", frame=fname, cv=cv, vlm=obs,
                       fused={k: fused[k] for k in ("left", "right", "bag_shift", "vlm_pulling")})
        log.info("tick %d  L=%s(cv %s/vlm %s) R=%s(cv %s/vlm %s)  vlm %sms cv %sms", self.tick_n,
                 fused["left"]["on"], fused["left"]["cv"], fused["left"]["vlm"],
                 fused["right"]["on"], fused["right"]["cv"], fused["right"]["vlm"],
                 (obs or {}).get("_ms"), cv["ms"])
        rule_calls = self.engine.update(now, dt, fused)
        done = await self.run_calls(rule_calls, now)

        if now - self.last_vitals >= self.cfg["sinks"].get("vitals_period_s", 2.0):
            self.last_vitals = now
            ev = self.engine.vitals.event(int(self.cfg["sinks"].get("vitals_period_s", 2.0) * 1000))
            self.log.write("vitals", **self.engine.vitals.summary())
            await self.emit([ev], {"name": "vitals"})

        if self.cfg["vlm"].get("use_director_llm") and (self.director_task is None or self.director_task.done()):
            ctx = {
                "observation": {s: {"touching": fused[s]["on"], "action": fused[s]["action"]} for s in ("left", "right")},
                "scene": (obs or {}).get("summary"),
                "state": self.engine.snapshot(now),
                "executed_this_tick": [{"name": c["name"], **{k: v for k, v in c["arguments"].items() if k != "origin"}}
                                       for c in done],
                "recent_calls": self.engine.history[-6:],
            }
            self.director_task = asyncio.create_task(self.direct(ctx))
        (self.dir / "state.json").write_text(json.dumps(self.engine.snapshot(now)))
        return True

    async def run(self, max_ticks: int | None = None):
        for _, s in self.sinks:
            if hasattr(s, "start"):
                await s.start()
        self.log.write("start", cfg={k: v for k, v in self.cfg.items()})
        log.info("run dir %s", self.dir)
        period = self.cfg.get("tick_s", 1.0)
        last = time.time()
        while not self.stop.is_set():
            t0 = time.time()
            ok = await self.tick(min(t0 - last, 5.0) if self.tick_n else period)
            last = t0
            if not ok and isinstance(self.frames, ReplayFrames):
                break
            if max_ticks and self.tick_n >= max_ticks:
                break
            await asyncio.sleep(max(0.0, period - (time.time() - t0)))
        if self.director_task:
            await asyncio.wait([self.director_task], timeout=10)
        # leave the VR clean
        await self.emit([{"type": "clear", "target": "all"}, {"type": "bleed", "intensity": 0},
                         {"type": "bile", "intensity": 0}], {"name": "shutdown", "source": "system"})
        self.log.write("stop", ticks=self.tick_n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--set", action="append", default=[], help="override, e.g. --set difficulty=hard")
    ap.add_argument("--dry-run", action="store_true", help="no VR / restyle output")
    ap.add_argument("--replay", help="folder of frames to replay instead of the Pi")
    ap.add_argument("--max-ticks", type=int)
    a = ap.parse_args()
    logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"), format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = config_mod.load(a.config, a.set)
    if a.replay:
        cfg["frames"]["replay_dir"] = a.replay
    d = Director(cfg, dry_run=a.dry_run)

    async def go():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, d.stop.set)
        await d.run(a.max_ticks)

    asyncio.run(go())


if __name__ == "__main__":
    main()
