"""End-to-end check on synthetic renders: calibration clicks -> tracking -> sim angles."""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from boxtracker import config, calib  # noqa: E402
from boxtracker.geometry import sim_angles  # noqa: E402
from boxtracker.synth import SynthScene, wobble  # noqa: E402
from boxtracker.tracker import Tracker  # noqa: E402


def run(eye=(0.0, -0.02, 0.60), target=(0.0, 0.0, 0.0), up=(0, 1, 0), frames=150, click_noise=1.5, raw=False):
    cfg = config.load("/nonexistent")
    if raw:
        cfg["filter"].update(angle_mincutoff=1e4, depth_mincutoff=1e4)
    scene = SynthScene(cfg, eye=eye, target=target, up=up)
    rng = np.random.default_rng(1)
    clicks = {k: (np.array(v) + rng.normal(0, click_noise, 2)).tolist() for k, v in scene.lid_clicks().items()}
    rvec, tvec, rms = calib.solve_lid(cfg, clicks)
    cfg["lid"]["rvec"], cfg["lid"]["tvec"] = rvec.tolist(), tvec.tolist()
    tr = Tracker(cfg)
    errs = {"yaw": [], "pitch": [], "depth": []}
    lost = 0
    jaw_ok = 0
    jaw_n = 0
    for f in range(frames):
        t = f / 50
        sticks = []
        truth = {}
        for i, tool in enumerate(cfg["tools"]):
            v, ins = wobble(t, i)
            covered = (f // 25 + i) % 2 == 1
            sticks.append({"tool": tool, "dir": v, "insertion": ins, "jaw_covered": covered})
            yaw, pitch = sim_angles(cfg["sim_ports"][tool["port"]], -v)
            truth[tool["id"]] = (yaw, pitch, ins - 0.008, covered)
        img = scene.render(sticks)
        poses, _ = tr.process(img, t)
        got = {p["id"]: p for p in poses}
        for tid, (yaw, pitch, depth, covered) in truth.items():
            p = got.get(tid)
            if p is None or tr.tools[[x["id"] for x in cfg["tools"]].index(tid)].status != "tracking":
                lost += 1
                continue
            if f > 10:
                errs["yaw"].append(abs(p["yaw"] - yaw))
                errs["pitch"].append(abs(p["pitch"] - pitch))
                errs["depth"].append(abs(p["depth"] - depth))
            if f % 25 > 5:
                jaw_n += 1
                jaw_ok += (p["jaw"] == 0.0) == covered
    out = {k: (math.degrees(np.median(v)) if k != "depth" else 1000 * np.median(v),
               math.degrees(np.percentile(v, 95)) if k != "depth" else 1000 * np.percentile(v, 95))
           for k, v in errs.items()}
    return out, lost, jaw_ok / max(1, jaw_n), rms


def test_synthetic_accuracy():
    # Overhead camera 50 cm above the lid, noisy clicks, rendered hands. The synthetic hands
    # partly cover band 1 from above, so these bounds are loose; the jaw is not checked here.
    out, lost, jaw, rms = run(eye=(0.0, -0.02, 0.50), click_noise=1.0)
    print("median/p95 errors (deg, deg, mm):", out, "lost frames:", lost, "lid rms", rms)
    assert lost < 10
    assert out["yaw"][0] < 5 and out["pitch"][0] < 5
    assert out["depth"][0] < 12


if __name__ == "__main__":
    test_synthetic_accuracy()
