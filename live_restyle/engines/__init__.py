"""Restyle engines. Each exposes `max_batch` and `process(frames, events) -> list[BGR]`."""
from __future__ import annotations


def load_engine(name: str, cfg: dict):
    if name == "passthrough":
        from .passthrough import Passthrough
        return Passthrough(cfg)
    if name == "edit":
        from .edit import EditEngine
        return EditEngine(cfg)
    if name == "hybrid":
        from .hybrid import HybridEngine
        return HybridEngine(cfg)
    raise ValueError(f"unknown engine {name!r}")
