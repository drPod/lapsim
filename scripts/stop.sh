#!/usr/bin/env bash
# Stop everything this project runs on the GPU box (frees all VRAM).
tmux kill-session -t restyle 2>/dev/null && echo "stopped tmux session 'restyle'" || echo "not running"
pkill -f "python -m live_restyle" 2>/dev/null || true
pkill -f "bin/mediamtx" 2>/dev/null || true
nvidia-smi --query-gpu=memory.used --format=csv,noheader 2>/dev/null || true
