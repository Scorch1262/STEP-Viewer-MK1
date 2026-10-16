"""Einstellungsdialog und dauerhafte Programmeinstellungen.

Die Einstellungen werden über QSettings im Benutzerprofil gespeichert und gelten
damit bei jedem Start – auch nach einem Update oder einer Neuinstallation:
  Windows: Registrierung HKEY_CURRENT_USER\\Software\\Scorch1262\\STEP-Viewer-MK1
  macOS:   ~/Library/Preferences/ (plist-Datei mit "STEP-Viewer-MK1" im Namen)
"""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .viewer import DEFAULT_MOUSE_MAP, MOUSE_BUTTONS

ACTIONS = (("rotate", "Drehen"), ("pan", "Verschieben"), ("zoom", "Zoomen"))
BUTTON_NAMES = {"left": "Linke Maustaste", "middle": "Mittlere Maustaste / Rad drücken", "right": "Rechte Maustaste"}


def load_mouse_settings(settings: QSettings) -> tuple[dict, bool]:
    mapping = dict(DEFAULT_MOUSE_MAP)
    for b in MOUSE_BUTTONS:
        v = settings.value(f"maus/{b}", mapping[b])
        if v in dict(ACTIONS):
            mapping[b] = v
    invert = settings.value("maus/rad_umkehren", False, type=bool)
    return mapping, invert


def save_mouse_settings(settings: QSettings, mapping: dict, invert: bool):
    for b in MOUSE_BUTTONS:
        settings.setValue(f"maus/{b}", mapping[b])
    settings.setValue("maus/rad_umkehren", invert)
    settings.sync()  # sofort dauerhaft schreiben


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Einstellungen")
        self.setMinimumWidth(460)
        self.settings = settings
        mapping, invert = load_mouse_settings(settings)

        box = QGroupBox("Maus im 3D-Fenster – Taste gedrückt halten und ziehen")
        form = QFormLayout(box)
        self.combos = {}
        for b in MOUSE_BUTTONS:
            cb = QComboBox()
            for key, text in ACTIONS:
                cb.addItem(text, key)
            cb.setCurrentIndex([k for k, _ in ACTIONS].index(mapping[b]))
            cb.currentIndexChanged.connect(self._check)
            form.addRow(BUTTON_NAMES[b] + ":", cb)
            self.combos[b] = cb
        self.invert = QCheckBox("Zoomrichtung des Mausrads umkehren")
        self.invert.setChecked(invert)
        form.addRow("", self.invert)
        hint = QLabel(
            "Kurzer Klick mit der linken Taste wählt immer ein Bauteil aus.<br>"
            "Mit der Dreh-Taste: <b>Shift</b> = verschieben, <b>Strg</b> = um die Blickachse drehen."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8a93a3;")
        form.addRow(hint)
        self.warn = QLabel("")
        self.warn.setStyleSheet("color:#ff8c1a;")
        form.addRow(self.warn)
        reset = QPushButton("Standard wiederherstellen")
        reset.clicked.connect(self._defaults)
        form.addRow("", reset)

        info = QLabel(
            "Die Einstellungen werden im Benutzerprofil gespeichert und gelten bei jedem Start, "
            "auch nach Updates oder einer Neuinstallation."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#8a93a3;")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Speichern")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self.ok_button = buttons.button(QDialogButtonBox.Ok)

        lay = QVBoxLayout(self)
        lay.addWidget(box)
        lay.addWidget(info)
        lay.addWidget(buttons)
        self._check()

    def mapping(self) -> dict:
        return {b: cb.currentData() for b, cb in self.combos.items()}

    def _check(self):
        has_rotate = "rotate" in self.mapping().values()
        self.warn.setText("" if has_rotate else "Mindestens eine Taste muss zum Drehen belegt sein.")
        self.ok_button.setEnabled(has_rotate)

    def _defaults(self):
        for b, cb in self.combos.items():
            cb.setCurrentIndex([k for k, _ in ACTIONS].index(DEFAULT_MOUSE_MAP[b]))
        self.invert.setChecked(False)

    def _accept(self):
        save_mouse_settings(self.settings, self.mapping(), self.invert.isChecked())
        self.accept()

