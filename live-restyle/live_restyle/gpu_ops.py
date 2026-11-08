"""Torch image ops used by the hybrid engine: colour LUT, flow warping, masks.

Images are float tensors in [0,1], shape (B,3,H,W), RGB.
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F


def to_tensor(imgs_bgr: list[np.ndarray], device, size: tuple[int, int] | None = None) -> torch.Tensor:
    t = torch.from_numpy(np.stack(imgs_bgr)).to(device, non_blocking=True)
    t = t[..., [2, 1, 0]].permute(0, 3, 1, 2).float() / 255.0
    if size is not None and (t.shape[-2], t.shape[-1]) != (size[1], size[0]):
        t = F.interpolate(t, size=(size[1], size[0]), mode="bilinear", antialias=True, align_corners=False)
    return t


def to_bgr(t: torch.Tensor) -> list[np.ndarray]:
    a = (t.clamp(0, 1) * 255 + 0.5).byte().permute(0, 2, 3, 1)[..., [2, 1, 0]].contiguous().cpu().numpy()
    return list(a)


def gaussian_blur(x: torch.Tensor, sigma: float) -> torch.Tensor:
    if sigma <= 0:
        return x
    r = max(1, int(sigma * 3))
    k = torch.exp(-torch.arange(-r, r + 1, device=x.device, dtype=x.dtype) ** 2 / (2 * sigma ** 2))
    k = k / k.sum()
    c = x.shape[1]
    x = F.conv2d(F.pad(x, (r, r, 0, 0), mode="replicate"), k.view(1, 1, 1, -1).repeat(c, 1, 1, 1), groups=c)
    x = F.conv2d(F.pad(x, (0, 0, r, r), mode="replicate"), k.view(1, 1, -1, 1).repeat(c, 1, 1, 1), groups=c)
    return x


# ------------------------------------------------------------------ colour LUT

def fit_lut(raw: torch.Tensor, styled: torch.Tensor, weight: torch.Tensor | None = None,
            bins: int = 33, sigma: float = 1.2) -> torch.Tensor:
    """Fit a 3D LUT mapping raw RGB → styled RGB from one aligned pair.

    raw, styled: (1,3,H,W); weight: (1,1,H,W) or None. Returns (3,bins,bins,bins)
    indexed [c, b, g, r] for grid_sample. Empty cells are filled by blurring
    numerator and count with increasing radius (normalised convolution)."""
    dev = raw.device
    r = raw[0].reshape(3, -1)
    s = styled[0].reshape(3, -1)
    w = torch.ones(r.shape[1], device=dev) if weight is None else weight.reshape(-1)
    idx = (r.clamp(0, 1) * (bins - 1)).round().long()
    flat = idx[2] * bins * bins + idx[1] * bins + idx[0]  # b,g,r order
    num = torch.zeros(3, bins ** 3, device=dev).index_add_(1, flat, s * w)
    cnt = torch.zeros(bins ** 3, device=dev).index_add_(0, flat, w)
    num = num.view(1, 3, bins, bins, bins)
    cnt = cnt.view(1, 1, bins, bins, bins)
    lut = None
    for sg in (sigma, sigma * 3, sigma * 8):
        n = _blur3d(num, sg)
        c = _blur3d(cnt, sg)
        est = n / c.clamp_min(1e-6)
        if lut is None:
            lut = est
            filled = c > 1e-3
        else:
            lut = torch.where(filled, lut, est)
            filled = filled | (c > 1e-3)
    # anything still empty → global mean colour
    mean = (s * w).sum(1) / w.sum().clamp_min(1)
    lut = torch.where(filled, lut, mean.view(1, 3, 1, 1, 1))
    return lut[0]


def _blur3d(v: torch.Tensor, sigma: float) -> torch.Tensor:
    r = max(1, int(sigma * 2.5))
    k = torch.exp(-torch.arange(-r, r + 1, device=v.device, dtype=v.dtype) ** 2 / (2 * sigma ** 2))
    k = k / k.sum()
    c = v.shape[1]
    for dim in range(3):
        shape = [1, 1, 1, 1, 1]
        shape[2 + dim] = -1
        kk = k.view(shape).repeat(c, 1, 1, 1, 1)
        pad = [0] * 6
        pad[2 * (2 - dim)] = r
        pad[2 * (2 - dim) + 1] = r
        v = F.conv3d(F.pad(v, pad, mode="replicate"), kk, groups=c)
    return v


def apply_lut(x: torch.Tensor, lut: torch.Tensor) -> torch.Tensor:
    """x: (B,3,H,W) RGB in [0,1]; lut from fit_lut."""
    B, _, H, W = x.shape
    g = (x.clamp(0, 1) * 2 - 1).permute(0, 2, 3, 1).reshape(B, 1, H, W, 3)  # (x=r, y=g, z=b)
    out = F.grid_sample(lut.unsqueeze(0).expand(B, -1, -1, -1, -1), g, mode="bilinear",
                        padding_mode="border", align_corners=True)
    return out.view(B, 3, H, W)


# ------------------------------------------------------------------ flow

def base_grid(B: int, H: int, W: int, device) -> torch.Tensor:
    ys, xs = torch.meshgrid(torch.arange(H, device=device, dtype=torch.float32),
                            torch.arange(W, device=device, dtype=torch.float32), indexing="ij")
    return torch.stack([xs, ys], 0).unsqueeze(0).expand(B, -1, -1, -1)


def warp(src: torch.Tensor, flow: torch.Tensor) -> torch.Tensor:
    """Backward warp: out(p) = src(p + flow(p)). flow (B,2,h,w) in pixels of its own
    resolution; it is resized to src resolution."""
    B, _, H, W = src.shape
    if flow.shape[-2:] != (H, W):
        sx, sy = W / flow.shape[-1], H / flow.shape[-2]
        flow = F.interpolate(flow, size=(H, W), mode="bilinear", align_corners=False)
        flow = torch.stack([flow[:, 0] * sx, flow[:, 1] * sy], 1)
    p = base_grid(B, H, W, src.device) + flow
    gx = p[:, 0] / (W - 1) * 2 - 1
    gy = p[:, 1] / (H - 1) * 2 - 1
    return F.grid_sample(src, torch.stack([gx, gy], -1), mode="bilinear", padding_mode="border",
                         align_corners=True)


def photometric_valid(a: torch.Tensor, b_warped: torch.Tensor, thr: float, soft: float = 0.05,
                      blur: float = 1.5) -> torch.Tensor:
    """Soft validity mask (B,1,H,W): 1 where a ≈ warped b."""
    d = (gaussian_blur(a, blur) - gaussian_blur(b_warped, blur)).abs().mean(1, keepdim=True)
    return ((thr + soft - d) / soft).clamp(0, 1)


class FlowModel:
    """RAFT (torchvision, BSD-3) at reduced resolution."""

    def __init__(self, device, kind: str = "raft_large", width: int = 512, iters: int = 12):
        from torchvision.models.optical_flow import Raft_Large_Weights, Raft_Small_Weights, raft_large, raft_small
        if kind == "raft_small":
            self.net = raft_small(weights=Raft_Small_Weights.DEFAULT)
        else:
            self.net = raft_large(weights=Raft_Large_Weights.DEFAULT)
        self.net = self.net.to(device).eval().half() if device.type == "cuda" else self.net.to(device).eval()
        self.device, self.width, self.iters = device, width, iters

    @torch.no_grad()
    def __call__(self, a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
        """Flow from a to b (for backward-warping b onto a). a,b: (B,3,H,W) [0,1].
        Returns flow (B,2,h,w) at the reduced resolution."""
        H, W = a.shape[-2:]
        w = self.width
        h = int(round(H * w / W / 8)) * 8
        a_ = F.interpolate(a, size=(h, w), mode="bilinear", antialias=True, align_corners=False) * 2 - 1
        b_ = F.interpolate(b, size=(h, w), mode="bilinear", antialias=True, align_corners=False) * 2 - 1
        dt = next(self.net.parameters()).dtype
        flows = self.net(a_.to(dt), b_.to(dt), num_flow_updates=self.iters)
        return flows[-1].float()
