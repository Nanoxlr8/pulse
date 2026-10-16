#!/usr/bin/env python3
"""PULSE global hotkey listener.

Registers a system-wide hotkey (default Alt+F) that opens PULSE, or brings
it to the front if it is already open. Runs hidden in the background.

The key is configurable in  ~\\.pulse\\hotkey.json  e.g.

    {"mods": ["alt"], "key": "F"}
    {"mods": ["ctrl", "alt"], "key": "P"}
    {"mods": ["win"], "key": "space"}

Change it, then run  restart-hotkey.cmd  (or sign out and in).
"""
import os
import sys
import json
import ctypes
import socket
import subprocess
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
PULSE_DIR = os.path.join(HOME, ".pulse")
CFG = os.path.join(PULSE_DIR, "hotkey.json")
LOG = os.path.join(PULSE_DIR, "hotkey.log")
APP = os.path.join(HERE, "app.pyw")

MODS = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002,
        "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

# Named keys; single characters fall back to their ASCII code.
VK = {"space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
      "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "insert": 0x2D,
      "delete": 0x2E, "home": 0x24, "end": 0x23,
      "pageup": 0x21, "pagedown": 0x22,
      "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
for _i in range(1, 25):
    VK["f%d" % _i] = 0x6F + _i          # F1 = 0x70


def log(msg):
    try:
        os.makedirs(PULSE_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            import time
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def load_cfg():
    cfg = {"mods": ["alt"], "key": "F"}
    try:
        with open(CFG, encoding="utf-8") as f:
            got = json.load(f)
        if isinstance(got.get("mods"), list) and got.get("key"):
            cfg = got
    except (OSError, ValueError):
        try:
            os.makedirs(PULSE_DIR, exist_ok=True)
            with open(CFG, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except OSError:
            pass
    return cfg


def resolve(cfg):
    mod = 0
    for m in cfg["mods"]:
        mod |= MODS.get(str(m).lower(), 0)
    k = str(cfg["key"])
    vk = VK.get(k.lower()) if len(k) > 1 else ord(k.upper())
    return mod, vk, "+".join(list(cfg["mods"]) + [k])


def single_instance():
    """Only one listener at a time, else the hotkey fires twice."""
    for port in (47821, 47822, 47823, 45241, 38721):
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", port))
            s.listen(1)
            return s            # held for the life of the process
        except OSError:
            continue
    return None


def launch():
    pyw = sys.executable
    if pyw.lower().endswith("python.exe"):
        cand = pyw[:-10] + "pythonw.exe"
        if os.path.exists(cand):
            pyw = cand
    try:
        subprocess.Popen([pyw, APP], cwd=HERE,
                         creationflags=0x00000008)     # DETACHED_PROCESS
    except Exception as e:
        log("launch failed: %s" % e)


def main():
    keep = single_instance()
    if keep is None:
        log("another hotkey listener is already running - exiting")
        return 0

    cfg = load_cfg()
    mod, vk, label = resolve(cfg)
    if not vk:
        log("bad key in hotkey.json: %r" % cfg)
        return 1

    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, mod | MOD_NOREPEAT, vk):
        err = ctypes.get_last_error()
        log("could not register %s (error %s) - another program most "
            "likely owns it. Edit %s and restart." % (label, err, CFG))
        return 1

    log("listening for %s" % label)
    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                launch()
    finally:
        user32.UnregisterHotKey(None, 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
