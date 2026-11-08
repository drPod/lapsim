from __future__ import annotations

import copy
from pathlib import Path

import yaml

# Difficulty presets. Seconds are wall-clock.
DIFFICULTY = {
    "easy": dict(
        bleed_start=0.30, bleed_step=0.10, bleed_prolonged_per_s=0.02, pull_step=0.15,
        hold_s=3.0, bile_start=0.35, duct_injury_after=3, random_per_min=0.3,
        bleed_ml_per_s=15.0, event_cooldown_s=60, fog_s=20, insufflation_s=15, bp_drop_s=20,
    ),
    "normal": dict(
        bleed_start=0.45, bleed_step=0.15, bleed_prolonged_per_s=0.04, pull_step=0.25,
        hold_s=5.0, bile_start=0.5, duct_injury_after=2, random_per_min=0.8,
        bleed_ml_per_s=25.0, event_cooldown_s=40, fog_s=30, insufflation_s=20, bp_drop_s=25,
    ),
    "hard": dict(
        bleed_start=0.6, bleed_step=0.2, bleed_prolonged_per_s=0.06, pull_step=0.3,
        hold_s=8.0, bile_start=0.6, duct_injury_after=1, random_per_min=1.5,
        bleed_ml_per_s=40.0, event_cooldown_s=25, fog_s=40, insufflation_s=30, bp_drop_s=30,
    ),
}

# Minimum seconds between two calls of the same tool (validated by the engine,
# whoever proposes the call: rules, the director LLM or the random scheduler).
COOLDOWNS = {
    "start_bleed": 0.0,
    "escalate_bleed": 2.0,
    "start_bile_leak": 0.0,
    "duct_injury": 10.0,
    "insufflation_loss": 0.0,
    "bp_drop": 0.0,
    "camera_fog": 0.0,
    "smoke": 8.0,
    "mark_controlled": 0.0,
    "staff_say": 6.0,
    "show_warning": 4.0,
    "no_action": 0.0,
}


def _set(d: dict, dotted: str, value):
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = yaml.safe_load(value) if isinstance(value, str) else value


def load(path: str | Path = "config.yaml", overrides: list[str] | None = None) -> dict:
    cfg = yaml.safe_load(Path(path).read_text()) if Path(path).exists() else {}
    cfg = copy.deepcopy(cfg)
    for o in overrides or []:
        k, v = o.split("=", 1)
        _set(cfg, k, v)
    cfg["preset"] = dict(DIFFICULTY[cfg.get("difficulty", "normal")])
    cfg["preset"].update(cfg.get("difficulty_overrides") or {})
    cfg["cooldowns"] = dict(COOLDOWNS, **(cfg.get("cooldowns") or {}))
    return cfg
