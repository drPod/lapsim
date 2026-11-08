#!/usr/bin/env bash
# Raspberry Pi 5 (Raspberry Pi OS Bookworm 64-bit) box-camera setup.
# Installs MediaMTX with the Pi camera source and runs it as a systemd service.
set -euo pipefail
VER="${MEDIAMTX_VERSION:-v1.21.3}"
ARCH=$(uname -m); case "$ARCH" in aarch64) PKG=linux_arm64 ;; armv7l) PKG=linux_armv7 ;; *) echo "unsupported arch $ARCH"; exit 1 ;; esac
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== checking camera"
if command -v rpicam-hello >/dev/null; then rpicam-hello --list-cameras || true; else echo "rpicam-apps missing: sudo apt install -y rpicam-apps"; fi

echo "== installing MediaMTX $VER ($PKG)"
tmp=$(mktemp -d)
curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/${VER}/mediamtx_${VER}_${PKG}.tar.gz" | tar xz -C "$tmp"
sudo install -m 755 "$tmp/mediamtx" /usr/local/bin/mediamtx
sudo mkdir -p /etc/mediamtx
sudo install -m 644 "$HERE/mediamtx.yml" /etc/mediamtx/mediamtx.yml

sudo tee /etc/systemd/system/mediamtx.service >/dev/null <<UNIT
[Unit]
Description=MediaMTX (lap-sim-vr box camera)
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/local/bin/mediamtx /etc/mediamtx/mediamtx.yml
Restart=always
RestartSec=2
User=$USER
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now mediamtx
sleep 2
systemctl --no-pager status mediamtx | head -5 || true
IP=$(hostname -I | awk '{print $1}')
echo
echo "MediaMTX is up. Test in a browser on the LAN:  http://$IP:8889/cam   (MediaMTX's own WebRTC page)"
echo "Then run the lap-sim-vr server with:            MEDIAMTX_URL=http://$IP:8889 npm start"
