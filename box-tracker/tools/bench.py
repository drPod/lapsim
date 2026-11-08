"""Time Tracker.process on synthetic frames (rendering excluded). Run on the Pi."""
import sys, time, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from boxtracker import config, calib
from boxtracker.synth import SynthScene, wobble
from boxtracker.tracker import Tracker
cfg = config.load("/nonexistent")
sc = SynthScene(cfg, eye=(0.0, -0.02, 0.50), target=(0, 0, 0), up=(0, 1, 0))
rv, tv, _ = calib.solve_lid(cfg, sc.lid_clicks())
cfg["lid"]["rvec"], cfg["lid"]["tvec"] = rv.tolist(), tv.tolist()
frames = []
for f in range(60):
    t = f / 56
    frames.append(sc.render([{"tool": tl, "dir": wobble(t, i)[0], "insertion": wobble(t, i)[1]} for i, tl in enumerate(cfg["tools"])]))
tr = Tracker(cfg)
ts = []
for rep in range(5):
    for i, fr in enumerate(frames):
        t0 = time.perf_counter(); tr.process(fr, (rep * 60 + i) / 56); ts.append(time.perf_counter() - t0)
ts = np.array(ts[60:]) * 1000
print(f"process(): median {np.median(ts):.2f} ms, p95 {np.percentile(ts, 95):.2f} ms, max {ts.max():.2f} ms, tracking {[x.status for x in tr.tools]}")
