#!/usr/bin/env bash
# Start Nemotron Nano 12B v2 VL (FP8) in vLLM as docker container "nemotron-vllm" on 127.0.0.1:8001.
# Flags follow the model card (trust-remote-code, modelopt FP8, video pruning off) plus Nemotron's own
# tool-call parser plugin shipped in the HF repo. VRAM is capped so live-restyle keeps the rest of the H100.
set -euo pipefail
MODEL=${MODEL:-nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-FP8}
IMAGE=${IMAGE:-vllm/vllm-openai:latest}
GPU_UTIL=${GPU_UTIL:-0.30}
HF=${HF_HOME:-$HOME/.cache/huggingface}
SNAP=$(ls -d "$HF"/hub/models--${MODEL//\//--}/snapshots/* | head -1)
QUANT=()
[[ $MODEL == *FP8* ]] && QUANT=(--quantization modelopt)
[[ $MODEL == *NVFP4* ]] && QUANT=(--quantization modelopt_fp4)
docker rm -f nemotron-vllm >/dev/null 2>&1 || true
docker run -d --name nemotron-vllm --gpus all --ipc=host --restart unless-stopped \
  -p 127.0.0.1:8001:8000 \
  -v "$HF":/root/.cache/huggingface \
  -e HF_HUB_OFFLINE=1 \
  "$IMAGE" \
  --model "$MODEL" --served-model-name nemotron-nano-vl \
  --trust-remote-code "${QUANT[@]}" --video-pruning-rate 0 \
  --max-model-len 16384 --max-num-seqs 8 --gpu-memory-utilization "$GPU_UTIL" \
  --limit-mm-per-prompt '{"image": 2, "video": 0}' \
  --enable-auto-tool-choice \
  --tool-parser-plugin "/root/.cache/huggingface/hub/models--${MODEL//\//--}/snapshots/$(basename "$SNAP")/llama_nemotron_toolcall_parser_no_streaming.py" \
  --tool-call-parser llama_nemotron_json \
  ${EXTRA_ARGS:-}
echo "waiting for vLLM on 127.0.0.1:8001 ..."
for i in $(seq 1 180); do
  curl -sf 127.0.0.1:8001/v1/models >/dev/null && { echo ready; exit 0; }
  docker ps -q -f name=nemotron-vllm | grep -q . || { docker logs --tail 50 nemotron-vllm; exit 1; }
  sleep 5
done
echo "timed out"; docker logs --tail 50 nemotron-vllm; exit 1
