"""boxtracker: camera tracker for a cardboard laparoscopy box.

  python -m boxtracker run [--source picam|synth] [--no-send]   start tracker + debug page
  python -m boxtracker calibrate                                 guided calibration (terminal)
  python -m boxtracker status                                    print live state
"""
import argparse
import asyncio
import json
import logging
import sys
import time
import urllib.request

from . import config as config_mod


def api(port, path, data=None):
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def cmd_run(args):
    from .app import App
    cfg = config_mod.load(args.config)
    if args.ws_url is not None:
        cfg["sender"]["ws_url"] = args.ws_url or None
    if args.port:
        cfg["server"]["port"] = args.port
    if args.source == "synth":
        from .sources import SynthSource
        source = SynthSource(cfg)
        if not cfg["lid"].get("rvec") and args.auto_calib:
            from . import calib
            rv, tv, rms = calib.solve_lid(cfg, source.scene.lid_clicks())
            cfg["lid"]["rvec"], cfg["lid"]["tvec"] = rv.tolist(), tv.tolist()
    else:
        from .sources import PiCamSource
        source = PiCamSource(cfg)
    app = App(cfg, source, config_path=args.config, send=not args.no_send)
    try:
        asyncio.run(app.main())
    finally:
        source.close()


def cmd_calibrate(args):
    """Terminal companion to the web wizard: prompts here, clicks on the debug page."""
    port = config_mod.load(args.config)["server"]["port"]
    try:
        st = api(port, "/api/state")
    except OSError:
        sys.exit("The tracker is not running. Start it first:  systemctl --user start boxtracker")
    host = "<pi-address>"
    try:
        import socket
        host = socket.gethostname() + ".local"
    except OSError:
        pass
    print(f"Open http://{host}:{port}/ (or http://<tailscale-ip>:{port}/) on a laptop or phone.")
    print("The camera picture there is where you click. Follow the prompts below or on the page.\n")
    api(port, "/api/cmd", {"cmd": "calib_start", "step": args.step})
    last = None
    while True:
        st = api(port, "/api/state")
        w = st["wizard"]
        line = (w["prompt"], w["msg"])
        if line != last:
            if w["msg"] and (last is None or w["msg"] != last[1]):
                print("   ->", w["msg"])
            if last is None or w["prompt"] != last[0]:
                print("\n" + w["prompt"])
                if w["step"] == "focus":
                    input("   Press Enter here to focus (or use the button) ... ")
                    api(port, "/api/cmd", {"cmd": "focus"})
                elif w["step"] == "intrinsics" and "optional" in w["prompt"]:
                    a = input("   Calibrate the lens with a phone checkerboard? [y/N] ").strip().lower()
                    api(port, "/api/cmd", {"cmd": "intrinsics_start" if a == "y" else "intrinsics_skip"})
            last = line
        if not w["active"]:
            print("\nCalibration finished." if w["step"] == "done" else "\nCalibration cancelled.")
            return
        time.sleep(0.3)


def cmd_status(args):
    port = config_mod.load(args.config)["server"]["port"]
    st = api(port, "/api/state")
    print(f'{st["fps"]} fps, {st["proc_ms"]} ms/frame, capture->pose {st["capture_latency_ms"]} ms, '
          f'sim {"connected" if st["send"]["connected"] else "offline"} {st["send"]["rate"]} msg/s, '
          f'send latency {st["send"]["latency_ms"]} ms')
    for t in st["tools"]:
        print(f'  {t["hole"]} {t["id"]:7s} {t["status"]:10s} {json.dumps(t["pose"])}')


def main():
    ap = argparse.ArgumentParser(prog="boxtracker")
    ap.add_argument("--config", default=config_mod.CONFIG_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--source", choices=["picam", "synth"], default="picam")
    r.add_argument("--no-send", action="store_true")
    r.add_argument("--ws-url", default=None, help="override the simulator WebSocket URL ('' = off)")
    r.add_argument("--port", type=int, default=None)
    r.add_argument("--auto-calib", action="store_true", help="synth only: fake the lid clicks")
    c = sub.add_parser("calibrate")
    c.add_argument("--step", default="focus", choices=["focus", "intrinsics", "lid", "colours", "wiggle", "jaw"])
    sub.add_parser("status")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    {"run": cmd_run, "calibrate": cmd_calibrate, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    main()
