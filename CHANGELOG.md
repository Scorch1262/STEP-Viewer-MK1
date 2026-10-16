# Änderungen

## 1.1.0 – Leistung bei großen Baugruppen
- Alle Bauteile werden gebündelt in nur noch zwei Zeichenobjekten dargestellt (Flächen und Kanten,
  vtkCompositePolyDataMapper) statt zwei Objekten pro Bauteil. Bei 5000 Teilen entfallen damit
  über 10.000 einzelne Zeichenaufrufe pro Bild – Drehen und Verschieben laufen deutlich flüssiger.
- SpaceMouse ohne Nachlauf: Die Bewegung wird einmal pro Bild als aktueller Zustand abgefragt statt
  als Warteschlange abgearbeitet; die Geschwindigkeit ist unabhängig von der Bildrate.
- Große Modelle: Kanten und Kantenglättung werden während der Bewegung ausgeblendet und danach sofort
  wieder eingeblendet (abschaltbar unter Ansicht → „Kanten beim Bewegen ausblenden“)
- macOS (Retina): während der Bewegung wird in halber Auflösung gerendert, im Stillstand wieder scharf
- Ein-/Ausblenden im Modellbaum: Änderungen werden gesammelt und in einem Schritt angewendet
  (Gruppe mit 5000 Teilen ausblenden: vorher ca. 50 s, jetzt sofort)
- Auswahl per Klick: Treffersuche über die Umrissboxen der Bauteile (wenige Millisekunden)
- Schnellere Berechnung der Einbaulagen beim Laden

## 1.0.3
- macOS: neue Offscreen-Darstellung. VTK rendert unsichtbar im Hintergrund, Qt zeigt das fertige
  Bild an. Damit hängt kein eigener VTK-OpenGL-Kontext mehr am Programmfenster – dieser hat die
  Oberfläche beim ersten Neuzeichnen nach dem Start blockiert (Hänger nach „Öffne …“).
- Windows/Linux zeichnen weiterhin direkt (schneller); umschaltbar mit STEPVIEWER_RENDER=native|offscreen
- Prüfung auf OpenGL 3.2 beim Start: ohne passende Grafik stürzt das Programm nicht mehr ab, sondern
  zeigt einen Hinweis in der 3D-Ansicht; Modellbaum und Infos funktionieren weiter
- Build: Starttest unter Windows mit Software-OpenGL (Mesa), unter macOS wird bei einem Hänger der
  native Aufrufstapel ins Log geschrieben

## 1.0.2
- macOS: Programm hing beim Öffnen einer Datei (Fenster ohne Reaktion). Das Laden lief in einem
  Hintergrund-Thread, der sich unter macOS mit der Oberfläche gegenseitig blockierte.
  Das Laden läuft jetzt im Haupt-Thread; die Oberfläche wird zwischendurch aktualisiert
  (Fortschrittsbalken), Benutzereingaben sind während des Ladens gesperrt.
- Kein systemweiter Warte-Mauszeiger mehr während des Ladens
- Ausführlicheres Protokoll der Ladeschritte (Lesen, Übertragen, Vernetzen, Darstellung)

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
