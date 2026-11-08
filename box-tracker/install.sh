#!/bin/sh
# Install on the Pi as the normal user (no sudo needed): venv + user service that starts at boot.
set -e
cd "$(dirname "$0")"
VENV="$HOME/boxtracker-venv"
[ -d "$VENV" ] || python3 -m venv --system-site-packages "$VENV"   # picamera2/libcamera come from apt
"$VENV/bin/pip" install -q opencv-python-headless aiohttp
[ "$(pwd)" = "$HOME/box-tracker" ] || { rm -rf "$HOME/box-tracker"; cp -r . "$HOME/box-tracker"; }
mkdir -p "$HOME/.config/systemd/user"
cp systemd/boxtracker.service "$HOME/.config/systemd/user/"
loginctl enable-linger "$USER" 2>/dev/null || echo "note: could not enable linger; the service starts at login instead of boot"
systemctl --user daemon-reload
systemctl --user enable --now boxtracker
echo "Debug page: http://$(hostname -I | awk '{print $1}'):8080/"
