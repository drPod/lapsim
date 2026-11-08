"""Hybrid engine: heavy keyframe edit + flow-anchored guide + fast diffusion refine + temporal blend.

Per batch of frames:
  1. keyframe K = (raw_K, styled_K, colour LUT raw→styled), produced asynchronously by the
     two-image edit model (Qwen-Image-Edit) and refreshed when the scene drifts;
  2. guide_t = styled_K warped onto frame t by optical flow (RAFT) where the warp is
     photometrically valid; elsewhere LUT(raw_t); instrument pixels get a shaft/metal-tip shader;
     blood/bile events are painted in;
  3. optional few-step SDXL img2img on the guide (fixed noise) to re-texture invalid areas;
  4. temporal blend with the previous output warped by frame-to-frame flow;
  5. endoscope grade + smoke composite.
"""
from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np
import torch
import torch.nn.functional as F

from .. import instruments
from ..gpu_ops import (FlowModel, apply_lut, fit_lut, gaussian_blur, photometric_valid, to_bgr,
                       to_tensor, warp)

log = logging.getLogger("live_restyle.hybrid")


class Keyframe:
    def __init__(self, raw, styled, lut, inst, t):
        self.raw, self.styled, self.lut, self.inst, self.t = raw, styled, lut, inst, t


class HybridEngine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.h = cfg.get("hybrid", {})
        self.dev = torch.device("cuda")
        o = cfg.get("output", {})
        self.W, self.H = int(o.get("width", 1024)), int(o.get("height", 576))
        self.max_batch = int(self.h.get("max_batch", 4))
        self.flow = FlowModel(self.dev, self.h.get("flow", "raft_large"), int(self.h.get("flow_width", 512)),
                              int(self.h.get("flow_iters", 12)))
        self.refiner = None
        if self.h.get("refine", {}).get("enabled", True):
            from ..refiner import Refiner
            self.refiner = Refiner(self.h.get("refine", {}), self.dev)
        self.editor = None
        self.ref_img = None
        self._load_editor()
        self.K: Keyframe | None = None
        self.K_lock = threading.Lock()
        self.k_req = None
        self.k_cond = threading.Condition()
        self.k_busy = False
        self.k_last_req = 0.0
        self.drift_count = 0
        self.prev_raw = None
        self.prev_out = None
        self.stats = {}
        self._vig = None
        if self.editor is not None:
            threading.Thread(target=self._keyframe_loop, daemon=True, name="keyframe").start()

    def _load_editor(self):
        e = self.cfg.get("edit", {})
        if not e or not self.h.get("keyframes", True):
            return
        from ..keyframe import EditModel
        self.editor = EditModel(e, "cuda")
        self.ref_img = cv2.imread(e["reference"])

    def update_config(self, cfg: dict):
        self.cfg = cfg
        self.h = cfg.get("hybrid", {})
        self.ref_img = cv2.imread(cfg.get("edit", {}).get("reference", "")) if self.editor else None

    # ------------------------------------------------------------ keyframes
    def request_keyframe(self, raw_bgr: np.ndarray):
        with self.k_cond:
            self.k_req = raw_bgr.copy()
            self.k_last_req = time.time()
            self.k_cond.notify()

    def _keyframe_loop(self):
        stream = torch.cuda.Stream()
        while True:
            with self.k_cond:
                while self.k_req is None:
                    self.k_cond.wait()
                raw = self.k_req
                self.k_req = None
                self.k_busy = True
            try:
                t0 = time.time()
                e = self.cfg["edit"]
                with torch.cuda.stream(stream):
                    styled = self.editor(raw, self.ref_img, e["prompt"], int(e.get("width", 1024)),
                                         int(e.get("height", 576)), seed=int(e.get("seed", 7)))[0]
                    self._set_keyframe(raw, styled)
                stream.synchronize()
                self.stats["keyframe_s"] = round(time.time() - t0, 2)
                log.info("keyframe ready in %.2fs", time.time() - t0)
            except Exception:
                log.exception("keyframe failed")
            finally:
                self.k_busy = False

    def _set_keyframe(self, raw_bgr: np.ndarray, styled_bgr: np.ndarray):
        raw = to_tensor([raw_bgr], self.dev, (self.W, self.H))
        styled = to_tensor([styled_bgr], self.dev, (self.W, self.H))
        shaft, _ = instruments.segment(cv2.resize(raw_bgr, (self.W, self.H)), self.cfg.get("instruments", {}))
        inst = torch.from_numpy(shaft).to(self.dev)[None, None]
        # LUT from the non-instrument area, at reduced resolution
        small = lambda t: F.interpolate(t, scale_factor=0.5, mode="bilinear", antialias=True)
        lut = fit_lut(small(raw), small(styled), 1 - small(inst), bins=int(self.h.get("lut_bins", 33)))
        with self.K_lock:
            self.K = Keyframe(raw, styled, lut, inst, time.time())

    # ------------------------------------------------------------ helpers
    def _inst_shader(self, raw: torch.Tensor, shaft: torch.Tensor, tip: torch.Tensor) -> torch.Tensor:
        ic = self.h.get("instrument", {})
        lum = raw.mean(1, keepdim=True)
        lum = (lum - lum.amin((2, 3), keepdim=True)) / (lum.amax((2, 3), keepdim=True) - lum.amin((2, 3), keepdim=True) + 1e-4)
        shaft_rgb = torch.tensor(ic.get("shaft_rgb", [0.07, 0.075, 0.08]), device=self.dev).view(1, 3, 1, 1)
        tip_rgb = torch.tensor(ic.get("tip_rgb", [0.62, 0.62, 0.64]), device=self.dev).view(1, 3, 1, 1)
        shade = 0.6 + 0.9 * lum
        col = shaft_rgb * shade
        col = col * (1 - tip) + tip_rgb * (0.5 + 0.8 * lum) * tip
        return col.clamp(0, 1)

    def _grade(self, x: torch.Tensor) -> torch.Tensor:
        p = self.cfg.get("post", {})
        if not p.get("enabled", True):
            return x
        x = x.clamp(0, 1) ** float(p.get("gamma", 1.08))
        g = p.get("gain_bgr", [0.96, 0.98, 1.04])
        x = x * torch.tensor([g[2], g[1], g[0]], device=self.dev).view(1, 3, 1, 1)
        bloom = float(p.get("bloom", 0.0))
        if bloom > 0:
            hi = ((x.amax(1, keepdim=True) - 0.8).clamp(0, 1) * 5)
            x = x + bloom * gaussian_blur(x * hi, max(2.0, self.W / 200))
        if self._vig is None or self._vig.shape[-1] != x.shape[-1]:
            from ..post import _vignette
            v = _vignette(x.shape[-2], x.shape[-1], float(p.get("vignette", 0.85)),
                          float(p.get("vignette_radius", 0.55)), float(p.get("vignette_softness", 0.65)))
            self._vig = torch.from_numpy(v[..., 0]).to(self.dev)[None, None]
        return (x * self._vig).clamp(0, 1)

    # ------------------------------------------------------------ main
    @torch.no_grad()
    def process(self, frames, events):
        t0 = time.time()
        imgs = [cv2.resize(f.img, (self.W, self.H), interpolation=cv2.INTER_AREA)
                if f.img.shape[:2] != (self.H, self.W) else f.img for f in frames]
        B = len(imgs)
        if self.K is None:
            if self.editor is not None and not self.k_busy and self.k_req is None:
                self.request_keyframe(imgs[-1])
            # no keyframe yet: show graded raw so the stream is alive
            out = self._grade(to_tensor(imgs, self.dev))
            return [events.post_composite(o) for o in to_bgr(out)]
        with self.K_lock:
            K = self.K
        raw = to_tensor(imgs, self.dev)
        segs = [instruments.segment(im, self.cfg.get("instruments", {})) for im in imgs]
        shaft = torch.from_numpy(np.stack([s for s, _ in segs])).to(self.dev)[:, None]
        tip = torch.from_numpy(np.stack([t for _, t in segs])).to(self.dev)[:, None]

        # --- anchor to keyframe
        Kraw = K.raw.expand(B, -1, -1, -1)
        fl = self.flow(raw, Kraw)                       # flow t → K
        warpK = warp(K.styled.expand(B, -1, -1, -1), fl)
        rawK_w = warp(Kraw, fl)
        instK_w = warp(K.inst.expand(B, -1, -1, -1), fl)
        valid = photometric_valid(raw, rawK_w, float(self.h.get("valid_thr", 0.06)))
        valid = valid * (1 - shaft) * (1 - instK_w).clamp(0, 1)
        valid = gaussian_blur(valid, 2.0)
        lutfill = apply_lut(raw, K.lut)
        guide = valid * warpK + (1 - valid) * lutfill
        inst_col = self._inst_shader(raw, shaft, tip)
        guide = guide * (1 - shaft) + inst_col * shaft

        # --- events painted into the guide (GPU)
        layers = events.fluid_layers(self.H, self.W)
        ev_mask = torch.zeros(1, 1, self.H, self.W, device=self.dev)
        for m, col, op, _seed in layers:
            mt = torch.from_numpy(m).to(self.dev)[None, None]
            c = torch.tensor([col[2], col[1], col[0]], device=self.dev, dtype=torch.float32).view(1, 3, 1, 1) / 255
            lum = guide.mean(1, keepdim=True)
            fluid = c * (0.6 + 0.8 * lum)
            guide = guide * (1 - mt * op) + fluid * mt * op
            ev_mask = torch.maximum(ev_mask, mt)

        # --- drift → keyframe refresh
        nonin = (1 - shaft)
        inval = 1 - ((valid * nonin).sum((1, 2, 3)) / nonin.sum((1, 2, 3)).clamp_min(1)).mean().item()
        self.stats["invalid_frac"] = round(inval, 3)
        rcfg = self.h.get("refresh", {})
        if inval > float(rcfg.get("invalid_frac", 0.3)):
            self.drift_count += 1
        else:
            self.drift_count = 0
        period = float(rcfg.get("period_s", 0) or 0)
        due = (self.drift_count >= int(rcfg.get("patience", 10))) or (period > 0 and time.time() - K.t > period)
        if due and not self.k_busy and time.time() - self.k_last_req > float(rcfg.get("min_interval_s", 5)):
            self.drift_count = 0
            self.request_keyframe(imgs[-1])

        # --- diffusion refine
        out = guide
        if self.refiner is not None:
            rc = self.h.get("refine", {})
            pos, neg = events.prompt_terms()
            prompt = self.cfg["prompt"]["positive"] + (", " + pos if pos else "")
            r = self.refiner(guide, prompt, self.cfg["prompt"].get("negative", ""),
                             float(rc.get("strength", 0.35)), int(rc.get("seed", 1234)))
            # keep the keyframe's detail where the anchor is valid and no event is happening
            keep = float(rc.get("keep_keyframe", 0.5)) * valid * (1 - ev_mask)
            out = r * (1 - keep) + warpK * keep

        # --- temporal blend with previous output (sequential through the batch)
        tc = self.h.get("temporal", {})
        lam = float(tc.get("blend", 0.6))
        if lam > 0:
            prev_raw = torch.cat([self.prev_raw, raw[:-1]]) if self.prev_raw is not None else None
            if prev_raw is not None:
                fp = self.flow(raw, prev_raw)            # flow t → t-1
                rawp_w = warp(prev_raw, fp)
                vt = photometric_valid(raw, rawp_w, float(tc.get("valid_thr", 0.04)))
                res = []
                last = self.prev_out
                for i in range(B):
                    pw = warp(last, fp[i:i + 1])
                    a = lam * vt[i:i + 1]
                    o = out[i:i + 1] * (1 - a) + pw * a
                    res.append(o)
                    last = o
                out = torch.cat(res)
        self.prev_raw = raw[-1:].clone()
        self.prev_out = out[-1:].clone()

        out = self._grade(out)
        res = [events.post_composite(o) for o in to_bgr(out)]
        self.stats["ms_per_frame"] = round((time.time() - t0) * 1000 / B, 1)
        return res
