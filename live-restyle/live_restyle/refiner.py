"""Fast per-frame diffusion refine pass (SDXL few-step img2img on the guide image).

Backends (config `refiner.model`):
  * sdxl_lightning  – SDXL base + ByteDance SDXL-Lightning 4-step UNet (OpenRAIL++-M, commercial OK)
  * sdxl_turbo      – stabilityai/sdxl-turbo (Stability non-commercial research licence)
  * sd_turbo        – stabilityai/sd-turbo   (Stability non-commercial research licence)

Same noise for every frame (fixed seed) so static regions denoise identically → low flicker.
"""
from __future__ import annotations

import logging
import time

import torch
import torch.nn.functional as F

log = logging.getLogger("live_restyle.refiner")


class Refiner:
    def __init__(self, cfg: dict, device):
        from diffusers import (AutoencoderKL, AutoencoderTiny, EulerDiscreteScheduler,
                               StableDiffusionXLImg2ImgPipeline, AutoPipelineForImage2Image,
                               UNet2DConditionModel)
        self.cfg = cfg
        self.device = device
        dtype = torch.float16
        model = cfg.get("model", "sdxl_lightning")
        self.model = model
        t0 = time.time()
        if model == "sdxl_lightning":
            from huggingface_hub import hf_hub_download
            from safetensors.torch import load_file
            base = "stabilityai/stable-diffusion-xl-base-1.0"
            steps = int(cfg.get("lightning_steps", 4))
            unet = UNet2DConditionModel.from_config(base, subfolder="unet").to(device, dtype)
            unet.load_state_dict(load_file(hf_hub_download(
                "ByteDance/SDXL-Lightning", f"sdxl_lightning_{steps}step_unet.safetensors"), device=str(device)))
            vae = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", torch_dtype=dtype)
            self.pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(
                base, unet=unet, vae=vae, torch_dtype=dtype, variant="fp16")
            self.pipe.scheduler = EulerDiscreteScheduler.from_config(
                self.pipe.scheduler.config, timestep_spacing="trailing")
            self.total_steps = steps
        elif model == "sdxl_turbo":
            self.pipe = AutoPipelineForImage2Image.from_pretrained(
                "stabilityai/sdxl-turbo", torch_dtype=dtype, variant="fp16")
            self.total_steps = int(cfg.get("turbo_steps", 4))
        elif model == "sd_turbo":
            self.pipe = AutoPipelineForImage2Image.from_pretrained(
                "stabilityai/sd-turbo", torch_dtype=dtype, variant="fp16")
            self.total_steps = int(cfg.get("turbo_steps", 4))
        else:
            raise ValueError(model)
        self.sdxl = model != "sd_turbo"
        if cfg.get("tiny_vae", False):
            self.pipe.vae = AutoencoderTiny.from_pretrained(
                "madebyollin/taesdxl" if self.sdxl else "madebyollin/taesd", torch_dtype=dtype)
        self.pipe.to(device)
        self.pipe.set_progress_bar_config(disable=True)
        if cfg.get("compile", False):
            self.pipe.unet = torch.compile(self.pipe.unet, mode="max-autotune-no-cudagraphs", fullgraph=False)
        self._emb_key = None
        self._emb = None
        log.info("refiner %s loaded in %.1fs", model, time.time() - t0)

    def _embeds(self, prompt: str, negative: str, B: int):
        key = (prompt, negative)
        if self._emb_key != key:
            with torch.no_grad():
                if self.sdxl:
                    pe, ne, ppe, npe = self.pipe.encode_prompt(
                        prompt=prompt, negative_prompt=negative, device=self.device,
                        num_images_per_prompt=1, do_classifier_free_guidance=False)
                    self._emb = (pe, ppe)
                else:
                    pe, _ = self.pipe.encode_prompt(prompt, self.device, 1, False)
                    self._emb = (pe, None)
            self._emb_key = key
        pe, ppe = self._emb
        out = {"prompt_embeds": pe.expand(B, -1, -1)}
        if ppe is not None:
            out["pooled_prompt_embeds"] = ppe.expand(B, -1)
        return out

    @torch.no_grad()
    def __call__(self, guide: torch.Tensor, prompt: str, negative: str, strength: float,
                 seed: int = 1234) -> torch.Tensor:
        """guide: (B,3,H,W) RGB [0,1] → (B,3,H,W) RGB [0,1]."""
        B, _, H, W = guide.shape
        # all frames share the same noise → identical denoising of unchanged content
        gens = [torch.Generator(self.device).manual_seed(seed) for _ in range(B)]
        out = self.pipe(image=guide.to(torch.float16), strength=strength,
                        num_inference_steps=self.total_steps, guidance_scale=0.0,
                        generator=gens, output_type="pt", height=H, width=W,
                        **self._embeds(prompt, negative, B)).images
        return out.float().clamp(0, 1)
