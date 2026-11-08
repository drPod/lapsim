#!/usr/bin/env bash
# One-time setup on the GPU box: venv + MediaMTX. Run from ~/live-restyle.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
[ -d .venv ] || uv venv -p 3.12 .venv
. .venv/bin/activate
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
uv pip install -r requirements.txt
# MediaMTX (RTSP in → WebRTC/WHEP, HLS out)
if [ ! -x bin/mediamtx ]; then
  mkdir -p bin && cd bin
  V=$(curl -s https://api.github.com/repos/bluenviron/mediamtx/releases/latest | python3 -c 'import sys,json;print(json.load(sys.stdin)["tag_name"])')
  curl -sL "https://github.com/bluenviron/mediamtx/releases/download/${V}/mediamtx_${V}_linux_amd64.tar.gz" | tar xz mediamtx
fi
echo "setup done"
