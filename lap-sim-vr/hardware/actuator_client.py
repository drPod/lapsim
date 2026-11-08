#!/usr/bin/env python3
"""Stub hardware daemon for the box trainer (FUTURE: pumps / servos on the Pi).

Connects to the lap-sim-vr event hub as role "hardware" and receives `actuator`
events, e.g. {"type":"actuator","device":"pump.bleed","value":0.8,"durationMs":5000}.
Right now it only logs them; replace `drive()` with GPIO/PWM code (gpiozero, pigpio,
or a motor HAT) when the pumps and servos exist.

    pip install websockets
    python3 hardware/actuator_client.py wss://<server>:5173/ws   # self-signed certs are accepted
"""
import asyncio, json, ssl, sys

import websockets  # pip install websockets


def drive(device: str, value: float, duration_ms: int | None) -> None:
    # TODO: map devices to hardware, e.g.
    #   pump.bleed  -> PWM duty cycle on the red-water peristaltic pump
    #   pump.bile   -> green-water pump into the gallbladder balloon
    #   servo.liver -> retract / lift the sponge liver
    print(f"[actuator] {device} = {value}" + (f" for {duration_ms} ms" if duration_ms else ""), flush=True)


async def main(url: str) -> None:
    ctx = None
    if url.startswith("wss://"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # LAN self-signed certs
    while True:
        try:
            async with websockets.connect(url, ssl=ctx) as ws:
                await ws.send(json.dumps({"type": "hello", "role": "hardware", "name": "box-pi"}))
                print("connected to", url, flush=True)
                async for raw in ws:
                    ev = json.loads(raw)
                    if ev.get("type") == "actuator":
                        drive(ev["device"], ev["value"], ev.get("durationMs"))
                    elif ev.get("type") == "reset":
                        print("[actuator] reset -> all pumps off, servos home", flush=True)
        except Exception as e:  # reconnect forever
            print("disconnected:", e, "- retrying in 2 s", flush=True)
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8787/ws"))
