"""Heavy two-image edit model: (raw frame = structure, approved target = style) → restyled frame.

Default: Qwen-Image-Edit-2511 (Apache-2.0) + lightx2v Lightning LoRA (Apache-2.0), 4 steps.
Also supports Qwen-Image-Edit-2509 and FLUX.1 Kontext [dev] (non-commercial licence) for comparison.
"""
from __future__ import annotations

import logging
import math
import time

import numpy as np
import torch
from PIL import Image

log = logging.getLogger("live_restyle.keyframe")

LIGHTNING = {
    "Qwen/Qwen-Image-Edit-2511": ("lightx2v/Qwen-Image-Edit-2511-Lightning", None),
    "Qwen/Qwen-Image-Edit-2509": ("lightx2v/Qwen-Image-Lightning",
                                  "Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors"),
}


def bgr_to_pil(img: np.ndarray) -> Image.Image:
    return Image.fromarray(img[..., ::-1].copy())


def pil_to_bgr(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("RGB"))[..., ::-1].copy()


class EditModel:
    def __init__(self, cfg: dict, device="cuda"):
        self.cfg = cfg
        self.device = device
        self.model_id = cfg.get("model", "Qwen/Qwen-Image-Edit-2511")
        self.steps = int(cfg.get("steps", 4))
        t0 = time.time()
        if "Kontext" in self.model_id:
            from diffusers import FluxKontextPipeline
            self.kind = "kontext"
            self.pipe = FluxKontextPipeline.from_pretrained(self.model_id, torch_dtype=torch.bfloat16)
        else:
            from diffusers import QwenImageEditPlusPipeline
            self.kind = "qwen"
            self.pipe = QwenImageEditPlusPipeline.from_pretrained(self.model_id, torch_dtype=torch.bfloat16)
            if cfg.get("lightning", True) and self.model_id in LIGHTNING:
                from diffusers import FlowMatchEulerDiscreteScheduler
                repo, wname = LIGHTNING[self.model_id]
                if cfg.get("lightning_weight_name"):
                    wname = cfg["lightning_weight_name"]
                # scheduler settings from the Qwen-Image-Lightning README
                sched_cfg = {
                    "base_image_seq_len": 256, "base_shift": math.log(3), "invert_sigmas": False,
                    "max_image_seq_len": 8192, "max_shift": math.log(3), "num_train_timesteps": 1000,
                    "shift": 1.0, "shift_terminal": None, "stochastic_sampling": False,
                    "time_shift_type": "exponential", "use_beta_sigmas": False, "use_dynamic_shifting": True,
                    "use_exponential_sigmas": False, "use_karras_sigmas": False,
                }
                self.pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(sched_cfg)
                kw = {"weight_name": wname} if wname else {}
                self.pipe.load_lora_weights(repo, **kw)
                self.pipe.fuse_lora()
                self.pipe.unload_lora_weights()
        self.pipe.to(device)
        self.pipe.set_progress_bar_config(disable=True)
        if cfg.get("compile", False):
            self.pipe.transformer = torch.compile(self.pipe.transformer, mode="max-autotune-no-cudagraphs")
        log.info("edit model %s loaded in %.1fs", self.model_id, time.time() - t0)

    @torch.no_grad()
    def __call__(self, raw_bgr: np.ndarray | list, ref_bgr: np.ndarray | None, prompt: str,
                 width: int, height: int, seed: int = 0, negative: str = " ") -> list[np.ndarray]:
        raws = raw_bgr if isinstance(raw_bgr, list) else [raw_bgr]
        gen = torch.Generator(self.device).manual_seed(seed)
        outs = []
        if self.kind == "kontext":
            for r in raws:
                im = self.pipe(image=bgr_to_pil(r), prompt=prompt, guidance_scale=self.cfg.get("guidance", 2.5),
                               num_inference_steps=self.steps, width=width, height=height,
                               generator=gen).images[0]
                outs.append(pil_to_bgr(im))
            return outs
        ref = [bgr_to_pil(ref_bgr)] if ref_bgr is not None else []
        for r in raws:
            im = self.pipe(image=[bgr_to_pil(r)] + ref, prompt=prompt, negative_prompt=negative,
                           true_cfg_scale=float(self.cfg.get("true_cfg", 1.0)),
                           num_inference_steps=self.steps, width=width, height=height,
                           generator=torch.Generator(self.device).manual_seed(seed)).images[0]
            outs.append(pil_to_bgr(im))
        return outs
