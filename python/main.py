# python/main.py
# Modulino UDP Bridge — routes every Modulino sensor event to Pure Data via UDP.
#
# Message formats sent to PD's [netreceive <port> 1]:
#   btn  <addr> <b1> <b2> <b3>   — press (0→1) toggles mute: snare / hat / synth
#   knob <addr> <delta> <press>  — delta (−1/0/1) nudges tempo ±2 BPM; press toggles play/stop
#   dist <addr> <mm>             — transposes the whole 303 pattern ±12 semitones
#                                  (past ~345 mm the pattern returns to its root)
#   move <addr> <ax> <ay> <az>  — accel as g×100 integers; ax → filter cutoff,
#                                  ay → filter resonance, shake → snare fill

import json
import os
import socket
import threading

from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI

_ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui")
ui = WebUI(assets_dir_path=_ui_dir)

# ── Config persistence ────────────────────────────────────────────────────────
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
_DEFAULT_CONFIG = {
    "udpHost":    os.environ.get("HOST_IP", "127.0.0.1"),
    "udpPort":    7400,
    "udpEnabled": True,
}
config = dict(_DEFAULT_CONFIG)


def _load_config():
    try:
        with open(_CONFIG_FILE) as f:
            data = json.load(f)
        for k in _DEFAULT_CONFIG:
            if k in data:
                config[k] = data[k]
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[config] load error: {e}")


def _save_config():
    try:
        with open(_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"[config] save error: {e}")


_load_config()

# ── UDP sender ────────────────────────────────────────────────────────────────
_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _send_pd(msg: str):
    #print(f"[udp] enabled={config.get('udpEnabled')} host={config['udpHost']!r} port={config['udpPort']} msg={msg!r}", flush=True)
    if not config.get("udpEnabled", True):
        return
    try:
        _udp.sendto((msg + "\n").encode(), (config["udpHost"], int(config["udpPort"])))
    except Exception as e:
        print(f"[udp] send error: {e}")


# ── Live device state ─────────────────────────────────────────────────────────
devices = {}   # addr_hex → {type, addr, ...values}
_joy_prev = {} # addr(int) → (nx, ny, pressed) previous state for flood prevention
_btn_prev = {} # addr(int) → (b0, b1, b2) previous state for edge detection


def _update(addr: int, dtype: str, values: dict):
    key = f"{addr:02x}"
    devices[key] = {"type": dtype, "addr": addr, **values}


# ── Bridge event handlers ─────────────────────────────────────────────────────
def on_device_found(addr: int, dtype: str):
    key = f"{addr:02x}"
    if key not in devices:
        devices[key] = {"type": dtype, "addr": addr}


def on_device_unknown(addr: int):
    key = f"{addr:02x}"
    devices[key] = {"type": "unknown", "addr": addr}


def on_btn_event(addr: int, b0: bool, b1: bool, b2: bool):
    state = (bool(b0), bool(b1), bool(b2))
    prev  = _btn_prev.get(addr, (False, False, False))
    _btn_prev[addr] = state
    if state != prev:
        _send_pd(f"btn {addr} {int(b0)} {int(b1)} {int(b2)}")
    _update(addr, "buttons", {"b0": bool(b0), "b1": bool(b1), "b2": bool(b2)})


def on_joy_event(addr: int, nx: float, ny: float, jp: bool):
    state = (round(nx, 3), round(ny, 3), bool(jp))
    if _joy_prev.get(addr) != state:
        _joy_prev[addr] = state
        _send_pd(f"joy {addr} {nx:.3f} {ny:.3f} {int(jp)}")
    _update(addr, "joystick", {"nx": state[0], "ny": state[1], "pressed": state[2]})


def on_knob_event(addr: int, delta: int, pressed: bool):
    _send_pd(f"knob {addr} {delta} {int(pressed)}")
    _update(addr, "knob", {"delta": delta, "pressed": bool(pressed)})


def on_dist_event(addr: int, mm: float):
    _send_pd(f"dist {addr} {mm:.1f}")
    _update(addr, "distance", {"mm": round(mm, 1)})


def on_imu_event(addr: int, ax: float, ay: float, az: float,
                 roll: float, pitch: float, yaw: float):
    _send_pd(f"move {addr} {int(ax*100)} {int(ay*100)} {int(az*100)}")
    _update(addr, "movement", {
        "ax": round(ax, 3), "ay": round(ay, 3), "az": round(az, 3),
        "roll": round(roll, 3), "pitch": round(pitch, 3), "yaw": round(yaw, 3),
    })


Bridge.provide("device_found",   on_device_found)
Bridge.provide("device_unknown", on_device_unknown)
Bridge.provide("btn_event",      on_btn_event)
Bridge.provide("joy_event",      on_joy_event)
Bridge.provide("knob_event",     on_knob_event)
Bridge.provide("dist_event",     on_dist_event)
Bridge.provide("imu_event",      on_imu_event)

# ── REST API ──────────────────────────────────────────────────────────────────
def get_state():
    return {"ok": True, "config": config, "devices": devices}


def post_config(body: dict):
    # udpPort is intentionally not accepted here — it can only be changed by
    # editing python/config.json directly and restarting the app.
    if "udpHost" in body:
        config["udpHost"] = str(body["udpHost"])
    if "udpEnabled" in body:
        config["udpEnabled"] = bool(body["udpEnabled"])
    _save_config()
    return {"ok": True, "config": config}


def post_devices(body: dict):
    addr = body.get("addr")
    dtype = body.get("type")
    if addr is None or dtype is None:
        return {"ok": False, "error": "addr and type required"}
    addr = int(addr)
    key = f"{addr:02x}"
    try:
        Bridge.call("configure_device", addr, dtype)
    except Exception as e:
        print(f"[bridge] configure_device error: {e}")
    devices[key] = {"type": dtype, "addr": addr}
    return {"ok": True, "device": devices[key]}


_rescan_lock = threading.Lock()


def post_rescan():
    if not _rescan_lock.acquire(blocking=False):
        return {"ok": True, "message": "scan in progress"}
    try:
        devices.clear()
        Bridge.call("rescan")
    finally:
        _rescan_lock.release()
    return {"ok": True}


ui.expose_api("GET",  "/api/state",   get_state)
ui.expose_api("POST", "/api/config",  post_config)
ui.expose_api("POST", "/api/devices", post_devices)
ui.expose_api("POST", "/api/rescan",  post_rescan)

App.run()