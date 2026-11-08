#!/usr/bin/env python3
"""Short H100 benchmark of Klein editing real trainer frames; leaves live service alone."""
import json
import time
from pathlib import Path
import torch
import yaml
from PIL import Image
from diffusers import Flux2KleinPipeline

root = Path(__file__).resolve().parents[1]
out = root / 'runs/klein'
out.mkdir(parents=True, exist_ok=True)
prompt = yaml.safe_load((root / 'config/restyle.yaml').read_text())['gemini']['prompt']
t0 = time.perf_counter()
pipe = Flux2KleinPipeline.from_pretrained('black-forest-labs/FLUX.2-klein-4B', torch_dtype=torch.bfloat16).to('cuda')
pipe.set_progress_bar_config(disable=True)
print('LOAD_SECONDS', time.perf_counter()-t0, flush=True)
with torch.inference_mode():
    t0 = time.perf_counter()
    embeds = pipe.encode_prompt(prompt=prompt, device='cuda', num_images_per_prompt=1, max_sequence_length=512)[0]
    torch.cuda.synchronize()
    print('PROMPT_SECONDS', time.perf_counter()-t0, flush=True)
    records = []
    for w,h in [(768,432), (512,288)]:
        for n,name in enumerate(['raw_moved.jpg', 'raw_real_scene.png', 'raw_real_scene2.png']):
            raw=Image.open(root/'refs'/name).convert('RGB').resize((w,h))
            torch.cuda.synchronize(); t0=time.perf_counter()
            img=pipe(image=raw,prompt_embeds=embeds,height=h,width=w,num_inference_steps=4,guidance_scale=1.,generator=torch.Generator('cuda').manual_seed(7)).images[0]
            torch.cuda.synchronize(); elapsed=time.perf_counter()-t0
            path=out/f'{w}_{n}.jpg'; img.save(path,quality=94)
            raw.save(out/f'raw_{w}_{n}.jpg',quality=94)
            record={'size':[w,h],'input':name,'seconds':round(elapsed,3),'output':str(path)}
            records.append(record); print(json.dumps(record),flush=True)
            (out/'results.json').write_text(json.dumps(records,indent=2))
