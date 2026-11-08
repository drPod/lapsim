# live-restyle

Live restyle of the laparoscopy trainer box camera into cholecystectomy-looking footage.

- Server: `python -m live_restyle.server --engine <gemini|openai|edit|hybrid|passthrough> --source <src>` (port 8090)
  - sources: `http://100.90.206.75:8080/stream.mjpg` (Pi box-tracker MJPEG, no need to stop it),
    `file:clip.mp4`, or push via `pi/sender.py` to `ws://<gpu>:8090/ingest`.
- Output: `/` viewer (raw | restyled, event buttons), `/frame/result.jpg` + `WS /ws/notify` (finished frames),
  `/stream/out.mjpg`, `WS /ws/stream?which=out` (crossfaded), WHEP `:8889/restyled/whep` via MediaMTX (`--rtsp`).
- Events: `POST /events {"type":"bleed","severity":0.7}` (bleed | smoke | bile_leak | clear), `/ws/control`.
- Config/prompts: `config/restyle.yaml` (hot reload: `POST /config/reload`).
- Keys from env only: `GEMINI_API_KEY`, `OPENAI_API_KEY` (GPU: `~/live-restyle/.env`, gitignored).
- GPU: `scripts/setup_gpu.sh`, `scripts/start.sh <engine> [--source ...]`, `scripts/stop.sh`.
- Bench: `bench/bench_api.py` (API engines: s/frame, $/frame, drift), `bench/bench_edit.py` (Qwen/Kontext on GPU).

Status: gemini/openai engines tested only against local mock APIs (no keys yet). Qwen edit/hybrid engines written, not yet run on the H100.
