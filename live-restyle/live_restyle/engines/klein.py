"""Local four-step Klein editor; reuse prompt embeddings and process newest frames."""
import time
from pathlib import Path
import cv2
import numpy as np
import torch
from PIL import Image
from diffusers import Flux2KleinPipeline


class KleinEngine:
    max_batch = 1

    def __init__(self, cfg):
        self.cfg = cfg
        self.options = cfg.get('klein', {})
        self.pipe = Flux2KleinPipeline.from_pretrained(
            'black-forest-labs/FLUX.2-klein-4B', torch_dtype=torch.bfloat16,
            cache_dir='/dev/shm/klein-cache/hub' if Path('/dev/shm/klein-cache/hub').exists() else None).to('cuda')
        self.pipe.set_progress_bar_config(disable=True)
        self.prompt = None
        self.embeds = None
        self.stats = {'ok': 0, 'last_s': 0, 'avg_s': 0}

    def update_config(self, cfg):
        self.cfg = cfg
        self.options = cfg.get('klein', {})

    def run(self, svc):
        ver = 0
        total = 0
        while svc.running:
            frame, new_ver = svc.raw.wait(ver, timeout=0.5)
            if frame is None or new_ver == ver:
                continue
            ver = new_ver
            t0 = time.perf_counter()
            try:
                pos, _ = svc.events.prompt_terms()
                prompt = self.cfg['gemini']['prompt'] + (' Also: '+pos if pos else '')
                with torch.inference_mode():
                    if prompt != self.prompt:
                        self.embeds = self.pipe.encode_prompt(prompt=prompt, device='cuda',
                            num_images_per_prompt=1, max_sequence_length=512)[0]
                        self.prompt = prompt
                    w,h = int(self.options.get('width',768)),int(self.options.get('height',432))
                    raw = Image.fromarray(cv2.cvtColor(frame.img,cv2.COLOR_BGR2RGB)).resize((w,h))
                    img = self.pipe(image=raw, prompt_embeds=self.embeds,width=w,height=h,
                        num_inference_steps=4,guidance_scale=1.,
                        generator=torch.Generator('cuda').manual_seed(7)).images[0]
                    result = cv2.cvtColor(np.asarray(img),cv2.COLOR_RGB2BGR)
                dt=time.perf_counter()-t0
                total += dt
                self.stats['ok'] += 1
                self.stats.update(last_s=round(dt,3),avg_s=round(total/self.stats['ok'],3))
                svc.engine_error = None
                svc.present(frame,svc.events.post_composite(result),{'gen_s':round(dt,3),'engine':'klein'})
            except Exception as exc:
                import logging
                logging.exception('Klein frame failed')
                svc.engine_error=repr(exc)
                time.sleep(0.5)
