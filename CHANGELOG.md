# Änderungen

## 1.0.1
- macOS: Programm hing beim Start (Dock-Symbol und Menüleiste sichtbar, Fenster ohne Reaktion).
  Ursache: die 3D-Ansicht hat schon gerendert, bevor das Fenster existierte. Die 3D-Ansicht
  startet jetzt erst, wenn das Fenster sichtbar ist; vorher werden keine Renderaufrufe ausgeführt.
- Dateien, die beim Start übergeben werden (Doppelklick, „Öffnen mit“), werden erst nach dem
  Start der 3D-Ansicht geladen
- Neue Protokolldatei mit Hänger-Wächter (Hilfe → Protokollordner öffnen)
  - macOS: ~/Library/Logs/STEP-Viewer-MK1/stepviewer.log
  - Windows: %LOCALAPPDATA%\STEP-Viewer-MK1\logs\stepviewer.log

## 1.0.0
- Erste Version des STEP-Viewer-MK1 als eigenständiges Programm (Qt + VTK + Open CASCADE)
- Öffnen von STEP, IGES, BREP und STL inkl. Baugruppen, Bauteilnamen und Farben
- Modellbaum mit Sichtbarkeit, Auswahl per Klick im 3D-Fenster, Info-Bereich mit Abmessungen
- Standardansichten, Darstellungsarten, Transparenz, Perspektive, Achsenkreuz, Hintergründe
- Messwerkzeug (Punkt-zu-Punkt-Abstand), PNG-Export der Ansicht
- Experimentelle SpaceMouse-Unterstützung
- GitHub-Actions-Build: Windows-Setup, Windows-Portable-ZIP, macOS-DMG/ZIP, automatisches Release
