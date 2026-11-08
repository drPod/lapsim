"""Keyboard input without extra packages: read /dev/input/event* directly.

Any USB keyboard plugged into the Pi works (put it on the floor as a foot pedal). The user
needs to be in the `input` group (Raspberry Pi OS default for the first user).
"""
import glob
import os
import select
import struct
import threading
import time

EVENT = struct.Struct("llHHi")
EV_KEY = 1
# Subset of linux/input-event-codes.h; enough for any sensible mapping.
CODES = {
    1: "KEY_ESC", 2: "KEY_1", 3: "KEY_2", 4: "KEY_3", 5: "KEY_4", 6: "KEY_5", 7: "KEY_6",
    8: "KEY_7", 9: "KEY_8", 10: "KEY_9", 11: "KEY_0", 15: "KEY_TAB", 16: "KEY_Q", 17: "KEY_W",
    18: "KEY_E", 19: "KEY_R", 20: "KEY_T", 21: "KEY_Y", 22: "KEY_U", 23: "KEY_I", 24: "KEY_O",
    25: "KEY_P", 28: "KEY_ENTER", 29: "KEY_LEFTCTRL", 30: "KEY_A", 31: "KEY_S", 32: "KEY_D",
    33: "KEY_F", 34: "KEY_G", 35: "KEY_H", 36: "KEY_J", 37: "KEY_K", 38: "KEY_L", 42: "KEY_LEFTSHIFT",
    44: "KEY_Z", 45: "KEY_X", 46: "KEY_C", 47: "KEY_V", 48: "KEY_B", 49: "KEY_N", 50: "KEY_M",
    53: "KEY_SLASH", 54: "KEY_RIGHTSHIFT", 56: "KEY_LEFTALT", 57: "KEY_SPACE", 97: "KEY_RIGHTCTRL",
    100: "KEY_RIGHTALT", 103: "KEY_UP", 105: "KEY_LEFT", 106: "KEY_RIGHT", 108: "KEY_DOWN",
}


class KeyReader:
    def __init__(self, pressed, lock):
        self.pressed = pressed      # shared set of key names
        self.lock = lock
        self.fds = {}
        self.devices = []
        self._stop = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="keys")
        self.thread.start()

    def _scan(self):
        for path in glob.glob("/dev/input/event*"):
            if path in self.fds.values():
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            name = ""
            try:
                with open(f"/sys/class/input/{os.path.basename(path)}/device/name") as f:
                    name = f.read().strip()
            except OSError:
                pass
            # keep only devices that report letter keys
            try:
                with open(f"/sys/class/input/{os.path.basename(path)}/device/capabilities/key") as f:
                    bits = int(f.read().split()[-1], 16)
            except (OSError, ValueError, IndexError):
                bits = 0
            if not bits & (1 << 33):    # KEY_F
                os.close(fd)
                continue
            self.fds[fd] = path
            self.devices.append(name or path)

    def _run(self):
        last_scan = 0
        while not self._stop:
            if time.monotonic() - last_scan > 5:
                self._scan()
                last_scan = time.monotonic()
            if not self.fds:
                time.sleep(1)
                continue
            r, _, _ = select.select(list(self.fds), [], [], 1.0)
            for fd in r:
                try:
                    data = os.read(fd, EVENT.size * 64)
                except OSError:
                    os.close(fd)
                    self.fds.pop(fd, None)
                    continue
                for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, typ, code, value = EVENT.unpack_from(data, i)
                    if typ != EV_KEY:
                        continue
                    name = CODES.get(code, f"KEY_{code}")
                    with self.lock:
                        if value:          # 1 = down, 2 = autorepeat
                            self.pressed.add(name)
                        else:
                            self.pressed.discard(name)
