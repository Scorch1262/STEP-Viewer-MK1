"""Optionale Unterstützung für 3Dconnexion-SpaceMouse-Geräte über HID.

Liest die Rohdaten der SpaceMouse direkt per hidapi aus. Ist kein Gerät
vorhanden oder wird es vom Treiber exklusiv belegt, passiert einfach nichts.
"""

from __future__ import annotations

import math
import struct
import time

from PySide6.QtCore import QThread, Signal

try:
    import hid  # Paket "hidapi"
except Exception:  # noqa: BLE001
    hid = None

VENDOR_3DCONNEXION = 0x256F
VENDOR_LOGITECH = 0x046D
LOGITECH_SPACEMICE = {
    0xC603, 0xC605, 0xC606, 0xC621, 0xC623, 0xC625, 0xC626, 0xC627,
    0xC628, 0xC629, 0xC62B, 0xC631, 0xC632, 0xC633, 0xC635, 0xC636, 0xC652,
}
FULL_SCALE = 350.0
DEADZONE = 0.04


def available() -> bool:
    return hid is not None


def find_device():
    if hid is None:
        return None
    best = None
    try:
        devices = hid.enumerate()
    except Exception:  # noqa: BLE001
        return None
    for d in devices:
        vid, pid = d.get("vendor_id"), d.get("product_id")
        if vid == VENDOR_3DCONNEXION or (vid == VENDOR_LOGITECH and pid in LOGITECH_SPACEMICE):
            # Bevorzugt die Schnittstelle "Multi-Axis Controller" (Usage Page 1 / Usage 8)
            if d.get("usage_page") == 1 and d.get("usage") == 8:
                return d
            best = best or d
    return best


def _axis(raw: int) -> float:
    v = max(-1.0, min(1.0, raw / FULL_SCALE))
    if abs(v) < DEADZONE:
        return 0.0
    s = (abs(v) - DEADZONE) / (1 - DEADZONE)
    # sanfte Kennlinie: feine Bewegungen langsam, volle Auslenkung schnell
    return math.copysign(0.4 * s + 0.6 * s * s, v)


class SpaceMouseThread(QThread):
    motion = Signal(float, float, float, float, float, float)
    button = Signal(int)
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._buttons = 0

    def stop(self):
        self._running = False
        self.wait(1000)

    def run(self):  # noqa: C901
        if hid is None:
            self.status.emit("SpaceMouse: Modul hidapi nicht verfügbar")
            return
        dev_info = find_device()
        if dev_info is None:
            self.status.emit("SpaceMouse: kein Gerät gefunden")
            return
        device = hid.device()
        try:
            device.open_path(dev_info["path"])
        except Exception:  # noqa: BLE001
            self.status.emit("SpaceMouse: Gerät belegt (Treiber) – Zugriff nicht möglich")
            return
        name = dev_info.get("product_string") or "SpaceMouse"
        self.status.emit(f"SpaceMouse verbunden: {name}")
        t = [0.0, 0.0, 0.0]
        r = [0.0, 0.0, 0.0]
        last_emit = 0.0
        try:
            while self._running:
                try:
                    data = device.read(64, 20)
                except Exception:  # noqa: BLE001
                    self.status.emit("SpaceMouse: Verbindung verloren")
                    break
                if data:
                    rid = data[0]
                    if rid == 1 and len(data) >= 7:
                        tx, ty, tz = struct.unpack("<hhh", bytes(data[1:7]))
                        t = [_axis(tx), _axis(ty), _axis(tz)]
                        if len(data) >= 13:
                            rx, ry, rz = struct.unpack("<hhh", bytes(data[7:13]))
                            r = [_axis(rx), _axis(ry), _axis(rz)]
                    elif rid == 2 and len(data) >= 7:
                        rx, ry, rz = struct.unpack("<hhh", bytes(data[1:7]))
                        r = [_axis(rx), _axis(ry), _axis(rz)]
                    elif rid == 3 and len(data) >= 2:
                        state = data[1] | ((data[2] << 8) if len(data) > 2 else 0)
                        pressed = state & ~self._buttons
                        self._buttons = state
                        for bit in range(16):
                            if pressed & (1 << bit):
                                self.button.emit(bit)
                now = time.monotonic()
                if now - last_emit >= 1 / 60 and any(t + r):
                    last_emit = now
                    self.motion.emit(t[0], t[1], t[2], r[0], r[1], r[2])
                if not data and any(t + r) and now - last_emit > 0.25:
                    # Gerät meldet beim Loslassen nicht immer 0 – Werte zurücksetzen
                    t = [0.0, 0.0, 0.0]
                    r = [0.0, 0.0, 0.0]
        finally:
            try:
                device.close()
            except Exception:  # noqa: BLE001
                pass
