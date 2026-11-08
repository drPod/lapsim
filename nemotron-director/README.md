# nemotron-director (WIP handoff)

AI director for the laparoscopic box trainer: Pi frame -> CV contact pre-check -> Nemotron VL Observer (JSON)
-> rules engine (contact->bleed/bile, escalation, pressure-hold control, random events, cooldowns, vitals)
-> Director LLM (fixed tool list, narration) -> VR relay (wss .../ws, role "agent") + live-restyle (POST :8090/events).

- `scripts/serve_vllm.sh` (on GPU): vLLM container `nemotron-vllm` on 127.0.0.1:8001 (UNTESTED).
- `python -m director.main [--dry-run] [--replay DIR] [--set difficulty=hard] [--set contact.fusion=cv]`
  logs to `logs/<run>/events.jsonl` + frames. Ctrl-C/SIGTERM stops and clears VR overlays.
- `python -m director.cvcheck img.jpg` CV debug overlay; `python -m tools.eval_contact labels.csv --vlm URL`.
- `tools/synth_frames.py` makes retracted-tip negatives; `pytest tests` (engine rules, 6 passing).
