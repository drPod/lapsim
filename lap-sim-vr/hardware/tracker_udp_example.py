#!/usr/bin/env python3
"""Minimal example of the tool-pose interface for the future box tracker.

Whatever measures the real instruments (instrumented trocars on an ESP32, or a
Pi-camera marker tracker) just sends one JSON datagram per frame to the
lap-sim-vr server's UDP port (default 9870). Angles in radians, depth in metres.

    python3 hardware/tracker_udp_example.py 192.168.1.20      # server IP
"""
import json, math, socket, sys, time

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9870
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def read_port_sensors(t):
    """Replace with real sensor reads: gimbal encoders (yaw, pitch), optical shaft
    sensor (depth, roll), handle jaw sensor, energy foot pedal."""
    return [
        {"id": "left", "port": "rsc", "tool": "grasper",
         "yaw": 0.10 * math.sin(t), "pitch": -0.05, "roll": 0.0, "depth": 0.09, "jaw": 1.0, "energy": False},
        {"id": "right", "port": "epigastric", "tool": "maryland",
         "yaw": 0.05, "pitch": 0.08 * math.sin(1.3 * t), "roll": 0.0, "depth": 0.10 + 0.01 * math.sin(t),
         "jaw": 0.5 + 0.5 * math.sin(2 * t), "energy": False},
    ]

t0 = time.time()
while True:
    t = time.time() - t0
    msg = {"type": "tools", "t": int(time.time() * 1000), "tools": read_port_sensors(t)}
    sock.sendto(json.dumps(msg).encode(), (HOST, PORT))
    time.sleep(1 / 100)  # 100 Hz
