#!/bin/sh
# Runs ON the GPU host: prints an nvidia-smi sample every ~0.5 s for the dashboard (GPU_STATS_CMD).
# Usage from the server box: GPU_STATS_CMD="ssh gpu 'sh -s' < scripts/gpu-stats.sh"
while true; do
  nvidia-smi --query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,power.limit,temperature.gpu,clocks.sm --format=csv,noheader,nounits | head -1
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits | while IFS=, read -r pid name mem; do
    pid=$(echo $pid); cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null | cut -c1-160 | tr ',' ' ')
    echo "proc,$pid,$name,$mem,$cmd"
  done
  nvidia-smi pmon -c 1 -s u | awk '!/^#/ {print "pmon," $2 "," $4 "," $5}'
  echo ---
  sleep 0.5
done
