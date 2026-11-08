import sys, cv2, yaml, time, numpy as np
sys.path.insert(0, '.')
from live_restyle.engines.hybrid import HybridEngine
from live_restyle.events import EventState
from live_restyle.frames import Frame
cfg = yaml.safe_load(open('config/restyle.yaml'))
cfg['hybrid'].update(device='cpu', keyframes=False, flow_width=384, flow_iters=6, max_batch=4)
cfg['hybrid']['refine']['enabled'] = False
cfg['output'].update(width=768, height=432)
eng = HybridEngine(cfg)
eng._set_keyframe(cv2.imread('refs/raw_real_scene.png'), cv2.imread('refs/target_real_scene.png'))
ev = EventState(cfg['events'])
cap = cv2.VideoCapture('/tmp/clips/gpt_synth.mp4')
frames = []
i = 0
while True:
    ok, im = cap.read()
    if not ok: break
    frames.append(Frame(i, im, 0, 0)); i += 1
outs = []
sel = frames[::3][:24]
for k in range(0, len(sel), 4):
    if k == 12: ev.apply({"type": "bleed", "severity": 0.8}); ev.events[list(ev.events)[0]].t0 -= 5
    t = time.time()
    outs += eng.process(sel[k:k+4], ev)
    print('batch', time.time() - t, eng.stats)
vw = cv2.VideoWriter('/tmp/clips/hyb_cpu.mp4', cv2.VideoWriter_fourcc(*'mp4v'), 10, (1536, 432))
for f, o in zip(sel, outs):
    vw.write(np.hstack([cv2.resize(f.img, (768, 432)), o]))
vw.release()
cv2.imwrite('/tmp/clips/hyb_a.jpg', np.hstack([cv2.resize(sel[5].img, (768, 432)), outs[5]]))
cv2.imwrite('/tmp/clips/hyb_b.jpg', np.hstack([cv2.resize(sel[20].img, (768, 432)), outs[20]]))
