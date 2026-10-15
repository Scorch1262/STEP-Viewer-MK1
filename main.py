"""Startpunkt des STEP-Viewer-MK1.

Aufruf:  python main.py [datei.step]
"""

from __future__ import annotations

import os
import sys


def resource_path(*parts: str) -> str:
    """Pfad zu mitgelieferten Dateien – funktioniert auch in der exe/Mac-App."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def selftest(path: str) -> int:
    """Lädt eine Datei ohne Fenster (für den Test im GitHub-Actions-Build).

    Ergebnis wird in selftest.txt im aktuellen Ordner geschrieben.
    """
    lines = []
    code = 0
    try:
        import stepviewer.viewer  # noqa: F401  – prüft Qt/VTK-Einbindung
        from stepviewer import VERSION
        from stepviewer.loader import ModelLoader

        model = ModelLoader().load(path)
        lines.append(f"Version {VERSION}")
        lines.append(f"Bauteile {model.parts}, verschieden {model.unique_parts}, Dreiecke {model.triangles}")
        if model.triangles == 0:
            code = 2
    except Exception as exc:  # noqa: BLE001
        import traceback

        lines.append("FEHLER: " + repr(exc))
        lines.append(traceback.format_exc())
        code = 1
    lines.append("OK" if code == 0 else "FEHLGESCHLAGEN")
    with open("selftest.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return code


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2])

    # --smoketest <datei>: Oberfläche starten, Datei laden, danach selbst beenden (für CI)
    smoke_file = None
    if len(sys.argv) >= 3 and sys.argv[1] == "--smoketest":
        smoke_file = os.path.abspath(sys.argv[2])
        sys.argv = [sys.argv[0]]

    from stepviewer.diagnostics import HangWatchdog, log_file, setup_logging

    setup_logging()
    import logging

    log = logging.getLogger("stepviewer.main")

    # Windows: eigene Taskleisten-Gruppe und eigenes Icon
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Scorch1262.STEPViewerMK1")
        except Exception:  # noqa: BLE001
            pass

    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QColor, QIcon, QPalette
    from PySide6.QtWidgets import QApplication

    from stepviewer import APP_NAME, ORG_NAME, VERSION
    from stepviewer.mainwindow import MainWindow

    class ViewerApp(QApplication):
        """Fängt unter macOS das Öffnen per Doppelklick/„Öffnen mit“ ab."""

        def __init__(self, argv):
            super().__init__(argv)
            self.window: MainWindow | None = None
            self.pending: list[str] = []

        def event(self, ev):  # noqa: N802
            if ev.type() == QEvent.FileOpen:
                path = ev.file()
                if self.window is not None:
                    self.window.open_file(path)
                else:
                    self.pending.append(path)
                return True
            return super().event(ev)

    # VTK nutzt ein natives Unterfenster – Qt soll daneben keine weiteren erzeugen
    QApplication.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings, True)
    log.info("Qt wird gestartet")
    app = ViewerApp(sys.argv)
    from PySide6.QtCore import QTimer

    watchdog = HangWatchdog()
    watchdog.start(QTimer)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(VERSION)
    app.setStyle("Fusion")
    app.setPalette(_dark_palette(QPalette, QColor, Qt))
    app.setStyleSheet(STYLE)

    icon = QIcon()
    for name in ("icon.png", "icon_256.png"):
        p = resource_path("assets", name)
        if os.path.isfile(p):
            icon.addFile(p)
    app.setWindowIcon(icon)

    log.info("Hauptfenster wird erstellt")
    win = MainWindow(icon)
    app.window = win
    win.show()
    win.raise_()
    win.activateWindow()
    log.info("Hauptfenster angezeigt")

    if smoke_file:
        _start_smoketest(app, win, smoke_file, log, log_file())

    files = [a for a in sys.argv[1:] if os.path.isfile(a)] + app.pending
    if files:
        win.open_file(os.path.abspath(files[0]))  # wird nach dem Start der 3D-Ansicht geladen
    code = app.exec()
    log.info("Programm beendet (Code %s)", code)
    return code


def _start_smoketest(app, win, path, log, logpath):
    """Prüft im Build, ob Fenster, 3D-Ansicht und Laden funktionieren."""
    import time

    from PySide6.QtCore import QTimer

    start = time.monotonic()
    win.open_file(path)

    def check():
        elapsed = time.monotonic() - start
        if win.viewport._failed:
            log.error("SMOKETEST: OpenGL 3.2 nicht verfügbar")
            app.exit(4)
        elif win.viewport._ready and win.model is not None:
            log.info("SMOKETEST OK nach %.1f s", elapsed)
            app.exit(0)
        elif elapsed > 60:
            log.error("SMOKETEST Zeitüberschreitung (3D bereit=%s, Modell=%s)",
                      win.viewport._ready, win.model is not None)
            app.exit(3)
        else:
            QTimer.singleShot(250, check)

    QTimer.singleShot(250, check)
    print(f"Smoketest läuft, Protokoll: {logpath}", flush=True)


def _dark_palette(QPalette, QColor, Qt):  # noqa: N803
    p = QPalette()
    base = QColor(30, 32, 38)
    window = QColor(38, 41, 48)
    text = QColor(222, 226, 233)
    accent = QColor(255, 140, 26)
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, window)
    p.setColor(QPalette.ToolTipBase, QColor(50, 54, 62))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor(48, 52, 60))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, Qt.white)
    p.setColor(QPalette.Highlight, accent)
    p.setColor(QPalette.HighlightedText, QColor(20, 20, 20))
    p.setColor(QPalette.Link, accent)
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor(110, 115, 125))
    return p


STYLE = """
QToolBar { background: #262930; border: none; border-bottom: 1px solid #1b1d22; spacing: 2px; padding: 3px; }
QToolBar QToolButton { color: #dee2e9; padding: 5px 9px; border-radius: 4px; }
QToolBar QToolButton:hover { background: #363a44; }
QToolBar QToolButton:checked { background: #ff8c1a; color: #161616; }
QToolBar::separator { background: #3a3e48; width: 1px; margin: 4px 6px; }
QMenuBar { background: #262930; color: #dee2e9; }
QMenuBar::item:selected { background: #363a44; }
QMenu { background: #2c2f37; color: #dee2e9; border: 1px solid #1b1d22; }
QMenu::item:selected { background: #ff8c1a; color: #161616; }
QDockWidget { color: #dee2e9; }
QDockWidget::title { background: #262930; padding: 5px; }
QTreeWidget { border: 1px solid #1b1d22; }
QHeaderView::section { background: #2c2f37; color: #aeb5c2; border: none; padding: 4px; }
QLabel#infoPanel { background: #1e2026; border: 1px solid #1b1d22; padding: 8px; }
QStatusBar { background: #262930; color: #aeb5c2; }
QProgressBar { border: 1px solid #3a3e48; border-radius: 3px; text-align: center; height: 14px; }
QProgressBar::chunk { background: #ff8c1a; }
"""


if __name__ == "__main__":
    sys.exit(main())
