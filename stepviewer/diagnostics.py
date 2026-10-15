"""Protokolldatei und Hänger-Wächter.

Protokoll:
  Windows: %LOCALAPPDATA%\\STEP-Viewer-MK1\\logs\\stepviewer.log
  macOS:   ~/Library/Logs/STEP-Viewer-MK1/stepviewer.log
  Linux:   ~/.local/state/STEP-Viewer-MK1/stepviewer.log

Reagiert die Oberfläche länger als einige Sekunden nicht, schreibt der Wächter
die Stelle, an der das Programm hängt, in die Protokolldatei.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import platform
import sys
import threading
import time

from . import APP_NAME, VERSION

log = logging.getLogger("stepviewer")
_fault_file = None


def log_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, APP_NAME, "logs")
    elif sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"), "Library", "Logs", APP_NAME)
    else:
        path = os.path.join(os.path.expanduser("~"), ".local", "state", APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def log_file() -> str:
    return os.path.join(log_dir(), "stepviewer.log")


def setup_logging():
    global _fault_file
    path = log_file()
    # alte Datei behalten (eine Generation)
    try:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            os.replace(path, path + ".1")
    except OSError:
        pass
    handlers = [logging.FileHandler(path, encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    try:
        _fault_file = open(path, "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(_fault_file, all_threads=True)
    except Exception:  # noqa: BLE001
        pass
    log.info("%s %s startet – Python %s, %s %s (%s)", APP_NAME, VERSION, platform.python_version(),
             platform.system(), platform.release(), platform.machine())
    log.info("Protokoll: %s", path)


class HangWatchdog:
    """Erkennt, wenn die Qt-Ereignisschleife hängt, und protokolliert den Ort."""

    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout
        self._beat = time.monotonic()
        self._reported = False
        self._stop = False

    def heartbeat(self):
        if self._reported:
            log.warning("Oberfläche reagiert wieder")
        self._beat = time.monotonic()
        self._reported = False

    def start(self, qtimer_cls):
        self._timer = qtimer_cls()
        self._timer.timeout.connect(self.heartbeat)
        self._timer.start(500)
        threading.Thread(target=self._run, name="Wächter", daemon=True).start()

    def _run(self):
        while not self._stop:
            time.sleep(1.0)
            if not self._reported and time.monotonic() - self._beat > self.timeout:
                self._reported = True
                log.error("Oberfläche reagiert seit %.0f s nicht – Aufrufstapel folgt", self.timeout)
                for h in logging.getLogger().handlers:
                    h.flush()
                if _fault_file is not None:
                    try:
                        faulthandler.dump_traceback(_fault_file, all_threads=True)
                        _fault_file.flush()
                    except Exception:  # noqa: BLE001
                        pass
