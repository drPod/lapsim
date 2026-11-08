# LapSim: an AI-directed laparoscopic surgery trainer

A cardboard box, a Raspberry Pi camera and two sticks become a laparoscopic cholecystectomy
(gallbladder removal) you watch in a Meta Quest. Every camera frame is restyled into real
surgery by an image model. NVIDIA Nemotron watches the same feed and directs the case with
tool calls: bleeding when you touch the gallbladder, staff call-outs, falling blood pressure.

```
Pi 5 camera in the box ──MJPEG──▶ live-restyle (H100: FLUX.2 Klein 4B, ~0.3 s/frame) ──▶ lap-sim-vr ──▶ Quest (WebXR OR)
            │                                                    ▲                          │
            └──frames──▶ nemotron-director (Nemotron Nano 12B VL) ┘ tool calls → events ─────┘
                                                                                             └─▶ /dashboard.html
```

| Folder | What it is |
|---|---|
| [`lap-sim-vr/`](lap-sim-vr) | WebXR operating room for the Quest, Node event server (WebSocket bus, REST, restyle relay) and the **AI pipeline dashboard** at `/dashboard.html` |
| [`live-restyle/`](live-restyle) | GPU service that turns raw box frames into surgical frames (FLUX.2 Klein 4B locally, Gemini image edit as an alternative), plus bleed / smoke / bile overlays driven by events |
| [`nemotron-director/`](nemotron-director) | Nemotron Nano 12B VL on vLLM: observes each frame (which instrument touches what), a rules engine, and an LLM director that picks tools from a fixed list; logs everything and streams a trace to the dashboard |
| [`box-tracker/`](box-tracker) | Pi-side camera service (MJPEG stream, snapshots) and the first stick tracker |

Each folder keeps its own README and its full git history.

## Run it

GPU host (1×H100 80 GB):

```sh
cd live-restyle && scripts/start.sh klein --config config/klein-demo.yaml --source http://<pi>:8080/stream.mjpg   # :8090
cd nemotron-director && scripts/serve_vllm.sh                                                                     # :8001
```

Server box (tunnel 8090 and 8001 from the GPU):

```sh
cd lap-sim-vr && npm install && npm run build
RESTYLE_URL=http://127.0.0.1:8090 \
GPU_STATS_CMD="ssh gpu 'sh -s' < scripts/gpu-stats.sh" npm start                 # :8787, open /dashboard.html
cd nemotron-director && python -m director.main                                # needs pyyaml httpx websockets opencv-python-headless numpy
```

The dashboard shows the raw box camera, the restyled surgeon's view, what Nemotron sees and
says about each frame, every tool call with its arguments, whether it was accepted and where it
was dispatched (VR headset, AI video), patient vitals and active complications, and live H100
telemetry split by model (SM %, VRAM, power).

API keys live in `.env` files that are gitignored; none are committed.
