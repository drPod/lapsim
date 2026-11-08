# box-tracker

Camera tracker for a cardboard laparoscopy trainer. Sticks poked through holes in a box lid
are tracked by a Raspberry Pi camera and streamed as instrument poses to the lap-sim-vr WebXR
simulator. The box only gives touch; the headset shows the virtual abdomen.

- **Build guide for the user:** [docs/BUILD_GUIDE.md](docs/BUILD_GUIDE.md)
- Runs on: Raspberry Pi 5 + Camera Module 3 (imx708), Raspberry Pi OS (trixie), Python 3.13,
  picamera2 from apt, OpenCV and aiohttp from pip in a `--system-site-packages` venv. No root needed.

## How it works

```
camera (2304x1296 full-FOV mode, ISP-scaled to 960x540, 56 fps, newest-frame-only queue)
  -> HSV colour mask per stick, in a window around the predicted bands
  -> connected components -> pick band 2 / band 1 (prediction, else ordered by distance from the pivot)
  -> pose solve with the pivot constraint (see below)
  -> sim yaw/pitch/depth (sim's own Tl/Dl conventions) -> One Euro filter
  -> {"type":"tools","tools":[...]} over WebSocket (also UDP / HTTP POST for a LAN instance)
```

**Pose from two bands + pivot.** Each stick pivots in a known hole `P` (from calibration). Band 2
(lower) and band 1 (upper) are 5 cm apart. Band 2 sits at `P + s·v` and band 1 at `P + (s + 0.05)·v`.
The two band rays and the camera centre span a plane that must contain the stick, so finding
`v, s` is a 1-D root search in that plane (`StickSolver.candidates`). It usually has two roots,
the stick leaning towards or away from the camera. Bounding `s` by the stick length removes the
wrong one. A small Levenberg-Marquardt polishes the result; while tracking, it warm-starts from
the previous frame. The direction comes from the bands' **lower edges**, which fingers never cover.
The depth comes from band 2's centroid, which has no edge-overshoot bias.

`depth = (stick_length - band2_from_top) - s - tip_to_hinge`, so the virtual jaw tip sits where the
physical stick tip is.

**Jaw.** The top 1.5 cm of each stick is a third band. The tracker measures how much of it is visible
at its predicted position; the thumb on top hides it and closes the jaws. The Jaw calibration step
records open and closed levels, and the switch is debounced over 3 frames. Keyboard fallback:
Z / M / X / Space. **Energy:** F, from any keyboard plugged into the Pi (read straight from
`/dev/input`) or from the debug page.

**Lost tracks.** For `lost_hold_s` (0.5 s) the last pose is held and still sent. After that the
tool is dropped from the messages, so the sim keeps its last pose, and the filters reset.

## Frames and simulator conventions

- **Lid frame:** origin at the lid centre, x to the user's right, y away from the user, z up.
- The simulator's abdomen frame (`client/src/abdomen/tools.js`, seen in the bundle as `gl`) has
  ports `umbilical, epigastric, rsc, raa` with `pos`/`aim`. The lid layout is that frame translated
  by `LID_CENTRE_IN_SIM = (0.02, -0.005)`: hole R = epigastric, L = rsc, A = raa, and the U dot =
  umbilical (no hole). The sim tool ids are `right` (epigastric), `left` (rsc) and `assist` (raa).
- yaw and pitch use the sim's own port frame (`d = aim - pos`, `r = d × +y`, `u = r × d`;
  `yaw = atan2(a·r, a·d)`, `pitch = asin(a·u)`). They are therefore relative to the port's nominal
  axis, as the sim expects. `box_to_sim.rotation` (3x3) remaps axis conventions, and `sim_ports`,
  `lid.points` and the tool → port mapping are all in the config if the sim changes.
- Roll is always 0 with colour bands (an ArUco flag would give roll; not built, since the user has
  no printer).
- WebSocket protocol: `{"type":"hello","role":"hardware","name":"box-tracker"}`, then
  `{"type":"tools","tools":[{id, port, yaw, pitch, roll, depth, jaw, energy}]}`. `tool` is only sent
  if set in the config, so the director panel stays free to change instruments.

## Calibration (debug page → Calibrate, or `python -m boxtracker calibrate`)

1. **Focus**: one autofocus sweep, then focus, exposure (capped at 10 ms against motion blur), gain
   and white balance are locked and saved.
2. **Lens** (optional): checkerboard on a phone (`/checker`, 9x6 inner corners), 15 different views
   → `calibrateCamera`. Without it, nominal imx708 intrinsics are used (f = 705 px at 960x540).
3. **Lid**: click U, R, L, A → `solvePnP` (SQPnP + iterative, refined with LM). OpenCV 5.0's IPPE gives
   wrong poses for this 4-point layout, so it isn't used.
4. **Colours**: click both bands of each stick → HSV ranges (circular hue statistics) and band area.
5. **Wiggle**: the stick's image lines all pass through the pivot. Intersecting about 60 of them
   (least squares) per stick pins each hole to sub-pixel accuracy, and the lid pose is refitted
   from those.
6. **Jaw**: open and closed visibility levels per stick.

Everything is saved to `~/.config/boxtracker/config.json`.

## Run / install

```sh
./install.sh                         # on the Pi: venv, user systemd service, linger (starts at boot)
systemctl --user status boxtracker   # logs: journalctl --user -u boxtracker -f
~/boxtracker-venv/bin/python -m boxtracker status
```

Debug page: `http://<pi>:8080/`: live annotated camera (MJPEG), poses, fps, latency, send rate and
the calibration wizard. Local development without hardware:

```sh
python -m boxtracker --config /tmp/c.json run --source synth --auto-calib --ws-url ws://127.0.0.1:8799/ws --port 8090
python tools/mock_sim.py 8799        # stand-in for the sim's /ws
python tests/test_synth.py           # end-to-end accuracy on rendered frames
python tools/bench.py                # per-frame processing time
```

To use a LAN instance of the sim instead of the remote preview, set `sender.ws_url` (or
`sender.udp = "host:9870"`, or `sender.http = "http://host:8787/api/events"`) in the config and
restart the service.
