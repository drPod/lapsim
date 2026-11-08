"""Configuration: defaults, load/save as JSON.

Frames used throughout:
  lid frame  - origin at the lid centre (top surface), x to the user's right, y away from the
               user (towards the far edge), z up out of the box. Metres.
  sim frame  - the simulator's abdomen frame (client/src/abdomen/tools.js). The lid frame is
               the sim frame translated, so directions map through `box_to_sim.rotation`
               (identity by default) and the hole layout copies the sim port positions 1:1.
"""
import copy
import json
import os

CONFIG_PATH = os.environ.get(
    "BOXTRACKER_CONFIG", os.path.expanduser("~/.config/boxtracker/config.json"))

# Nominal imx708 (Camera Module 3, standard FOV) intrinsics for the full-FOV 2304x1296 mode
# scaled to the output size: f = 4.74 mm / (1.4 um * 2 binning) = 1693 px at 2304 wide.
def nominal_K(width, height, sensor_width=2304):
    f = 4.74e-3 / 2.8e-6 * width / sensor_width
    return [[f, 0, width / 2], [0, f, height / 2], [0, 0, 1]]


# Copied from the simulator bundle (tools.js `gl`): pivot position and the point the
# trocar nominally aims at, in the sim abdomen frame.
SIM_PORTS = {
    "umbilical": {"pos": [0.01, -0.085, 0.10], "aim": [-0.01, -0.004, -0.004]},
    "epigastric": {"pos": [-0.075, 0.07, 0.11], "aim": [-0.01, -0.012, 0.0]},
    "rsc": {"pos": [0.085, -0.05, 0.11], "aim": [0.005, -0.004, 0.0]},
    "raa": {"pos": [0.12, 0.06, 0.085], "aim": [0.03, 0.04, 0.0]},
}

# Lid centre in the sim frame (x, y). Lid point = sim point - this.
LID_CENTRE_IN_SIM = [0.02, -0.005]

# Colour presets in OpenCV HSV (H 0..179). `h` is a list of [lo, hi] hue ranges.
COLOUR_PRESETS = {
    "blue": {"h": [[95, 130]], "s": 80, "v": 40},
    "green": {"h": [[38, 88]], "s": 70, "v": 35},
    "red": {"h": [[0, 8], [168, 179]], "s": 110, "v": 60},
    "pink": {"h": [[150, 175]], "s": 70, "v": 60},
    "orange": {"h": [[5, 20]], "s": 140, "v": 80},
    "purple": {"h": [[125, 155]], "s": 60, "v": 35},
}

STICK = {
    "length": 0.30,           # total stick length, m
    "jaw_from_top": 0.0075,   # band centres, measured from the top (held) end
    "band1_from_top": 0.0875,
    "band2_from_top": 0.1375,
    "band_height": 0.015,
    "diameter": 0.007,
}

DEFAULTS = {
    "camera": {
        "width": 960, "height": 540, "sensor_size": [2304, 1296], "fps": 56,
        "hflip": False, "vflip": False,
        # Filled in by the Focus step; null = automatic.
        "lens_position": None, "exposure_us": None, "gain": None, "colour_gains": None,
    },
    "intrinsics": {"K": None, "dist": [0, 0, 0, 0, 0], "calibrated": False, "rms": None},
    "lid": {
        # Marks drawn on the lid (lid frame, metres). U is a black dot, the rest are holes.
        "points": {
            "U": [-0.01, -0.08], "R": [-0.095, 0.075], "L": [0.065, -0.045], "A": [0.10, 0.065],
        },
        "click_order": ["U", "R", "L", "A"],
        "rvec": None, "tvec": None, "reproj_px": None,
        "pivot_z": -0.003,   # pivot sits inside the cardboard, a few mm below the top
    },
    "box_to_sim": {
        # Rotation taking lid-frame directions to sim-frame directions (row-major 3x3).
        "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    },
    "sim_ports": SIM_PORTS,
    "tools": [
        {"id": "right", "port": "epigastric", "hole": "R", "colour": "blue", "hand": "left",
         "hsv": None, "stick": dict(STICK), "jaw_open_score": None, "jaw_closed_score": None,
         "band_area": None, "tool": None},
        {"id": "left", "port": "rsc", "hole": "L", "colour": "green", "hand": "right",
         "hsv": None, "stick": dict(STICK), "jaw_open_score": None, "jaw_closed_score": None,
         "band_area": None, "tool": None},
        {"id": "assist", "port": "raa", "hole": "A", "colour": "red", "hand": "assistant",
         "hsv": None, "stick": dict(STICK), "jaw_open_score": None, "jaw_closed_score": None,
         "band_area": None, "tool": None},
    ],
    "depth": {"scale": 1.0, "offset": 0.0, "min": 0.0, "max": 0.25, "tip_to_hinge": 0.008},
    "filter": {"angle_mincutoff": 1.5, "angle_beta": 0.6, "depth_mincutoff": 1.5,
               "depth_beta": 4.0, "dcutoff": 1.0},
    "tracking": {"lost_hold_s": 0.5, "max_residual_px": 5.0, "min_band_area": 10,
                 "roi_radius_m": 0.28, "jaw_debounce_frames": 3, "max_tilt_deg": 75},
    "keys": {
        # Linux input key names (any keyboard plugged into the Pi) and browser KeyboardEvent.code
        # on the debug page.
        "energy": ["KEY_F", "KeyF"],
        "jaw_close_all": ["KEY_SPACE", "Space"],
        "jaw_close": {"right": ["KEY_Z", "KeyZ"], "left": ["KEY_M", "KeyM"],
                      "assist": ["KEY_X", "KeyX"]},
        "energy_tools": None,   # null = every tool gets the energy flag
    },
    "sender": {
        "ws_url": "wss://8787-f3f85f6360e0.replicas.dev/ws",
        "role": "hardware", "name": "box-tracker",
        "udp": None,          # e.g. "192.168.1.20:9870" for a LAN instance
        "http": None,         # e.g. "http://192.168.1.20:8787/api/events"
        "max_rate": 60, "send_roll": True, "send_lost": False,
    },
    "server": {"host": "0.0.0.0", "port": 8080, "mjpeg_fps": 15, "jpeg_quality": 70},
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("points", "sim_ports"):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load(path=CONFIG_PATH):
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(path):
        with open(path) as f:
            cfg = _merge(cfg, json.load(f))
    for t in cfg["tools"]:
        t["stick"] = _merge(STICK, t.get("stick"))
    if not cfg["intrinsics"]["K"]:
        c = cfg["camera"]
        cfg["intrinsics"]["K"] = nominal_K(c["width"], c["height"], c["sensor_size"][0])
    return cfg


def save(cfg, path=CONFIG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)
