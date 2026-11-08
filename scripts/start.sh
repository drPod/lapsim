#!/usr/bin/env bash
# Start the restyle service (and MediaMTX for WHEP) in a tmux session "restyle".
#   scripts/start.sh [engine] [extra server args...]     e.g. scripts/start.sh openai
#   scripts/start.sh edit --source file:runs/synth.mp4
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p runs
ENGINE=${1:-openai}; shift || true
[ -f .env ] && set -a && . ./.env && set +a      # OPENAI_API_KEY lives here (chmod 600), never in git
tmux kill-session -t restyle 2>/dev/null || true
tmux new-session -d -s restyle -n server ". .venv/bin/activate; set -a; [ -f .env ] && . ./.env; set +a; python -m live_restyle.server --engine $ENGINE --rtsp rtsp://127.0.0.1:8554/restyled $* 2>&1 | tee -a runs/server.log"
tmux new-window -t restyle -n mediamtx "bin/mediamtx config/mediamtx.yml 2>&1 | tee -a runs/mediamtx.log"
sleep 2
echo "viewer:  http://$(tailscale ip -4 2>/dev/null || hostname -I | awk '{print $1}'):8090/"
echo "WHEP:    http://<gpu-ip>:8889/restyled/whep"
