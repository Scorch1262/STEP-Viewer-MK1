"""3D-Ansicht auf Basis von VTK, eingebettet in ein Qt-Fenster."""

from __future__ import annotations

import logging
import math
import os
import sys
from typing import Optional

# Qt-Bindung für die VTK-Einbettung festlegen (vor dem Import!)
import vtkmodules.qt

vtkmodules.qt.PyQtImpl = "PySide6"

log = logging.getLogger("stepviewer.viewer")

# Darstellungsart:
#  "native"    – VTK zeichnet direkt in ein natives Unterfenster (schnell; Windows/Linux)
#  "offscreen" – VTK rendert unsichtbar, Qt zeigt das fertige Bild an (macOS).
#                Unter macOS hängt ein eigener VTK-OpenGL-Kontext am Qt-Fenster
#                die Oberfläche beim Neuzeichnen auf.
# Überschreibbar mit der Umgebungsvariable STEPVIEWER_RENDER=native|offscreen
RENDER_MODE = os.environ.get("STEPVIEWER_RENDER", "").lower() or (
    "offscreen" if sys.platform == "darwin" else "native")
OFFSCREEN = RENDER_MODE == "offscreen"

# Diese Module müssen importiert werden, damit Rendering und Text funktionieren
import vtkmodules.vtkInteractionStyle  # noqa: F401,E402
import vtkmodules.vtkRenderingFreeType  # noqa: F401,E402
import vtkmodules.vtkRenderingOpenGL2  # noqa: F401,E402
import vtkmodules.vtkRenderingContextOpenGL2  # noqa: F401,E402

from PySide6.QtCore import Qt, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402
from vtkmodules.vtkCommonCore import vtkUnsignedCharArray  # noqa: E402
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor  # noqa: E402
from vtkmodules.vtkCommonDataModel import vtkMultiBlockDataSet, vtkPolyData  # noqa: E402
from vtkmodules.vtkCommonMath import vtkMatrix4x4  # noqa: E402
from vtkmodules.vtkCommonTransforms import vtkTransform  # noqa: E402
from vtkmodules.vtkFiltersGeneral import vtkTransformPolyDataFilter  # noqa: E402
from vtkmodules.vtkFiltersSources import vtkLineSource, vtkSphereSource  # noqa: E402
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera  # noqa: E402
from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget  # noqa: E402
from vtkmodules.vtkIOImage import vtkPNGWriter  # noqa: E402
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor  # noqa: E402
from vtkmodules.vtkRenderingCore import vtkLightKit  # noqa: E402
from vtkmodules.vtkRenderingCore import (  # noqa: E402
    vtkRenderWindow,
    vtkCompositeDataDisplayAttributes,
    vtkCompositePolyDataMapper,
    vtkActor,
    vtkBillboardTextActor3D,
    vtkCellPicker,
    vtkPolyDataMapper,
    vtkRenderer,
    vtkWindowToImageFilter,
)

from .loader import LoadedModel, ModelNode  # noqa: E402

HIGHLIGHT = (1.0, 0.55, 0.10)
EDGE_COLOR = (0.08, 0.08, 0.10)
MEASURE_COLOR = (1.0, 0.85, 0.15)

BACKGROUNDS = {
    "dunkel": ((0.12, 0.13, 0.16), (0.30, 0.33, 0.39)),
    "hell": ((0.78, 0.81, 0.86), (0.98, 0.98, 1.00)),
    "creo": ((0.36, 0.43, 0.55), (0.85, 0.88, 0.93)),
}

# Blickrichtung (von wo auf das Modell geschaut wird) und Oben-Vektor, Z ist oben
VIEWS = {
    "iso": ((1, -1, 1), (0, 0, 1)),
    "vorne": ((0, -1, 0), (0, 0, 1)),
    "hinten": ((0, 1, 0), (0, 0, 1)),
    "links": ((-1, 0, 0), (0, 0, 1)),
    "rechts": ((1, 0, 0), (0, 0, 1)),
    "oben": ((0, 0, 1), (0, 1, 0)),
    "unten": ((0, 0, -1), (0, -1, 0)),
}


IDENTITY = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


class PartActors:
    """Ein Bauteil als Block im gemeinsamen Flächen- und Kanten-Datensatz."""

    __slots__ = ("node", "faces", "edges", "has_colors", "index", "bounds", "visible", "selected")

    def __init__(self, node: ModelNode, faces, edges, has_colors: bool, index: int):
        self.node = node
        self.faces = faces  # vtkPolyData-Block (Flächen)
        self.edges = edges  # vtkPolyData-Block (Kanten)
        self.has_colors = has_colors
        self.index = index
        self.bounds = faces.GetBounds()
        self.visible = True
        self.selected = False


def _transformed(poly, matrix):
    """Kopie eines Netzes in Einbaulage (Normalen werden mitgedreht)."""
    out = vtkPolyData()
    if tuple(matrix) == IDENTITY:
        out.ShallowCopy(poly)
        return out
    m = vtkMatrix4x4()
    m.DeepCopy(matrix)
    t = vtkTransform()
    t.SetMatrix(m)
    f = vtkTransformPolyDataFilter()
    f.SetInputData(poly)
    f.SetTransform(t)
    f.Update()
    out.ShallowCopy(f.GetOutput())
    return out


class _Style(vtkInteractorStyleTrackballCamera):
    """Links drehen, Mitte/Shift verschieben, Rad zoomen, Klick = auswählen."""

    def __init__(self, viewport: "Viewport"):
        super().__init__()
        self.viewport = viewport
        self._press_pos = None
        self.AddObserver("LeftButtonPressEvent", self._on_left_press)
        self.AddObserver("LeftButtonReleaseEvent", self._on_left_release)
        self.AddObserver("RightButtonPressEvent", self._on_right_press)
        self.AddObserver("RightButtonReleaseEvent", self._on_right_release)
        self.AddObserver("StartInteractionEvent", lambda *a: self.viewport.begin_interaction())
        self.AddObserver("EndInteractionEvent", lambda *a: self.viewport.end_interaction(200))

    def _on_left_press(self, obj, event):
        self._press_pos = self.GetInteractor().GetEventPosition()
        self.OnLeftButtonDown()

    def _on_left_release(self, obj, event):
        self.OnLeftButtonUp()
        pos = self.GetInteractor().GetEventPosition()
        if self._press_pos is not None:
            dx = abs(pos[0] - self._press_pos[0])
            dy = abs(pos[1] - self._press_pos[1])
            if dx <= 3 and dy <= 3:
                self.viewport.handle_click(pos[0], pos[1])
        self._press_pos = None

    # Rechte Maustaste: verschieben (wie in vielen CAD-Viewern)
    def _on_right_press(self, obj, event):
        self.StartPan()

    def _on_right_release(self, obj, event):
        self.EndPan()


class Viewport(QVTKRenderWindowInteractor):
    partPicked = Signal(object)  # ModelNode oder None
    measured = Signal(float, tuple)  # Abstand, (dx, dy, dz)
    measureStatus = Signal(str)

    _suppress_winid = False

    def __init__(self, parent=None):
        if OFFSCREEN:
            rw = vtkRenderWindow()
            rw.SetOffScreenRendering(1)
            # Die VTK-Klasse fragt winId() ab – das würde ein natives Fenster
            # erzeugen. Im Offscreen-Modus soll das Widget ein normales Qt-Widget bleiben.
            Viewport._suppress_winid = True
            try:
                super().__init__(parent, rw=rw)
            finally:
                Viewport._suppress_winid = False
            self.setAttribute(Qt.WA_PaintOnScreen, False)
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
            self.setAttribute(Qt.WA_NoSystemBackground, True)
        else:
            super().__init__(parent)
        # Vor dem Anzeigen des Fensters darf VTK nicht rendern.
        self._ready = False
        self._failed = False
        self._Iren.EnableRenderOff()  # auch Mausaktionen rendern erst nach dem Start
        self._render_scale = None
        if OFFSCREEN:
            # Renderwünsche des Interaktors (Drehen, Zoomen …) als Qt-Neuzeichnen ausführen
            self._Iren.AddObserver("RenderEvent", lambda *a: self.update())
            # Mauskoordinaten passend zur aktuellen Renderauflösung umrechnen
            self._getPixelRatio = self._scale
        log.info("Darstellung: %s", RENDER_MODE)
        self.setFocusPolicy(Qt.StrongFocus)
        self.renderer = vtkRenderer()
        self.renderer.GradientBackgroundOn()
        self.renderer.SetUseFXAA(True)
        # CAD-typische Beleuchtung (Haupt-, Aufhell-, Gegen- und Kopflicht), folgt der Kamera:
        # gleich geneigte Flächen bekommen so unterschiedliche Helligkeit
        self.light_kit = vtkLightKit()
        self.light_kit.SetKeyLightIntensity(0.9)
        self.light_kit.AddLightsToRenderer(self.renderer)
        self.render_window = self.GetRenderWindow()
        self.render_window.AddRenderer(self.renderer)
        self.render_window.SetMultiSamples(0)

        self.style = _Style(self)
        self.style.SetDefaultRenderer(self.renderer)
        self.GetRenderWindow().GetInteractor().SetInteractorStyle(self.style)

        self.picker = vtkCellPicker()
        self.picker.SetTolerance(0.0005)

        self.parts: list[PartActors] = []
        self._part_by_flat: dict = {}
        self.faces_actor: Optional[vtkActor] = None
        self.edges_actor: Optional[vtkActor] = None
        self.face_attrs = vtkCompositeDataDisplayAttributes()
        self.edge_attrs = vtkCompositeDataDisplayAttributes()
        self._edge_cells = 0
        self._interacting_hidden_edges = False
        self.lod_enabled = True
        self._lod_timer = QTimer(self)
        self._lod_timer.setSingleShot(True)
        self._lod_timer.timeout.connect(self.end_interaction)
        self.model: Optional[LoadedModel] = None
        self.display_mode = "schattiert_kanten"
        self.transparent = False
        self.background = "dunkel"
        self.set_background("dunkel")

        # Koordinatenachsen unten links
        self.axes = vtkAxesActor()
        self.axes.SetShaftTypeToCylinder()
        self.axes.SetTotalLength(1.0, 1.0, 1.0)
        self.axes_widget = vtkOrientationMarkerWidget()
        self.axes_widget.SetOrientationMarker(self.axes)
        self.axes_widget.SetInteractor(self.GetRenderWindow().GetInteractor())
        self.axes_widget.SetViewport(0.0, 0.0, 0.16, 0.22)
        self._axes_visible = True

        # Messen
        self.measure_mode = False
        self.measure_points: list = []
        self.measure_actors: list = []

        self.camera = self.renderer.GetActiveCamera()
        self.camera.ParallelProjectionOn()

    # -- Lebenszyklus --------------------------------------------------------
    def initialize(self) -> bool:
        """Wird aufgerufen, sobald das Hauptfenster sichtbar ist."""
        if self._ready or self._failed:
            return self._ready
        log.info("VTK wird initialisiert …")
        rw = self.GetRenderWindow()
        try:
            supported = rw.SupportsOpenGL()
        except Exception:  # noqa: BLE001
            supported = 1
        if not supported:
            self._failed = True
            log.error("OpenGL 3.2 wird von diesem System nicht unterstützt – 3D-Ansicht deaktiviert")
            self.update()
            return False
        if OFFSCREEN:
            self._resize_offscreen()
        self.Initialize()
        if not OFFSCREEN:
            self._Iren.EnableRenderOn()
        self._ready = True
        self._enable_axes()
        log.info("VTK initialisiert")
        return True

    # -- Offscreen-Modus (macOS) ---------------------------------------------
    def winId(self):  # noqa: N802
        if Viewport._suppress_winid:
            return 0
        return super().winId()

    def paintEngine(self):  # noqa: N802
        if OFFSCREEN:
            return QWidget.paintEngine(self)
        return super().paintEngine()

    def _scale(self) -> float:
        """Renderauflösung je logischem Pixel (Retina: 2, bei Bewegung ggf. 1)."""
        return self._render_scale or self.devicePixelRatioF()

    def _resize_offscreen(self):
        scale = self._scale()
        w = max(1, int(round(self.width() * scale)))
        h = max(1, int(round(self.height() * scale)))
        rw = self.GetRenderWindow()
        rw.SetDPI(int(round(72 * scale)))
        rw.SetSize(w, h)
        self._Iren.SetSize(w, h)

    def resizeEvent(self, ev):  # noqa: N802
        if OFFSCREEN:
            self._resize_offscreen()
            self._Iren.ConfigureEvent()
            self.update()
            return
        super().resizeEvent(ev)

    def _paint_message(self, text: str):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(30, 32, 38))
        if text:
            p.setPen(QColor(200, 205, 215))
            p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, text)
        p.end()

    def paintEvent(self, ev):  # noqa: N802
        if self._failed:
            self._paint_message(
                "3D-Ansicht nicht verfügbar:\nDieses System bietet kein OpenGL 3.2.\n"
                "Bitte Grafiktreiber aktualisieren.")
            return
        if not self._ready:
            if OFFSCREEN:
                self._paint_message("")
            return
        if not OFFSCREEN:
            super().paintEvent(ev)
            return
        try:
            rw = self.GetRenderWindow()
            rw.Render()
            w, h = rw.GetSize()
            data = vtkUnsignedCharArray()
            rw.GetRGBACharPixelData(0, 0, w - 1, h - 1, 0, data)
            buf = memoryview(data).tobytes()
            img = QImage(buf, w, h, w * 4, QImage.Format_RGBA8888)
            img = img.flipped(Qt.Vertical) if hasattr(img, "flipped") else img.mirrored(False, True)
            img.setDevicePixelRatio(self._scale())
            p = QPainter(self)
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawImage(0, 0, img)
            p.end()
        except Exception:  # noqa: BLE001
            log.exception("Offscreen-Darstellung fehlgeschlagen")

    def Finalize(self):  # noqa: N802
        if not self._ready:
            return
        try:
            self.GetRenderWindow().Finalize()
        except Exception:  # noqa: BLE001
            log.exception("Finalize fehlgeschlagen")

    def _enable_axes(self):
        if self._axes_visible:
            self.axes_widget.SetEnabled(1)
            self.axes_widget.InteractiveOff()
        self.render()

    # -- Modell --------------------------------------------------------------
    def clear(self):
        for a in (self.faces_actor, self.edges_actor):
            if a is not None:
                self.renderer.RemoveActor(a)
        self.faces_actor = self.edges_actor = None
        self.parts.clear()
        self._part_by_flat.clear()
        self._edge_cells = 0
        self.face_attrs = vtkCompositeDataDisplayAttributes()
        self.edge_attrs = vtkCompositeDataDisplayAttributes()
        self.clear_measurements()
        self.model = None

    def set_model(self, model: LoadedModel) -> dict:
        """Erzeugt die Darstellung und liefert {id(node): PartActors}.

        Alle Bauteile kommen in EINEN Flächen- und EINEN Kanten-Datensatz
        (vtkMultiBlockDataSet). VTK zeichnet sie gebündelt mit wenigen
        Zeichenaufrufen – entscheidend bei Baugruppen mit tausenden Teilen.
        """
        self.clear()
        self.model = model
        leaves = []

        def visit(node: ModelNode):
            if node.mesh_key:
                leaves.append(node)
            for c in node.children:
                visit(c)

        visit(model.root)
        faces_mb = vtkMultiBlockDataSet()
        edges_mb = vtkMultiBlockDataSet()
        faces_mb.SetNumberOfBlocks(len(leaves))
        edges_mb.SetNumberOfBlocks(len(leaves))
        mapping = {}
        for i, node in enumerate(leaves):
            mesh = model.meshes[node.mesh_key]
            f = _transformed(mesh.faces, node.matrix)
            e = _transformed(mesh.edges, node.matrix)
            faces_mb.SetBlock(i, f)
            edges_mb.SetBlock(i, e)
            self._edge_cells += e.GetNumberOfCells()
            part = PartActors(node, f, e, mesh.has_face_colors, i)
            self.parts.append(part)
            self._part_by_flat[i + 1] = part  # flacher Index: 0 = Wurzel
            mapping[id(node)] = part

        fm = vtkCompositePolyDataMapper()
        fm.SetInputDataObject(faces_mb)
        fm.SetCompositeDataDisplayAttributes(self.face_attrs)
        fm.SetScalarModeToUseCellFieldData()
        fm.SelectColorArray("Farben")
        fm.SetColorModeToDirectScalars()
        fm.ScalarVisibilityOn()
        fm.SetResolveCoincidentTopologyToPolygonOffset()
        fm.SetRelativeCoincidentTopologyPolygonOffsetParameters(1.0, 1.0)
        fa = vtkActor()
        fa.SetMapper(fm)
        prop = fa.GetProperty()
        prop.SetInterpolationToPhong()
        prop.SetAmbient(0.18)
        prop.SetDiffuse(0.78)
        prop.SetSpecular(0.25)
        prop.SetSpecularPower(30)
        prop.BackfaceCullingOff()

        em = vtkCompositePolyDataMapper()
        em.SetInputDataObject(edges_mb)
        em.SetCompositeDataDisplayAttributes(self.edge_attrs)
        em.ScalarVisibilityOff()
        ea = vtkActor()
        ea.SetMapper(em)
        ea.GetProperty().SetLineWidth(1.2)
        ea.GetProperty().LightingOff()
        ea.PickableOff()

        self.faces_actor, self.edges_actor = fa, ea
        self.renderer.AddActor(fa)
        self.renderer.AddActor(ea)
        for p in self.parts:
            self._style_part(p)
        self._apply_display()
        self.set_view("iso", fit=True)
        log.info("Darstellung: %d Bauteile in 2 Zeichenobjekten", len(self.parts))
        return mapping

    # -- Darstellung ---------------------------------------------------------
    def set_display_mode(self, mode: str):
        self.display_mode = mode
        self._apply_display()
        self.render()

    def set_transparent(self, on: bool):
        self.transparent = on
        self._apply_display()
        self.render()

    def _apply_display(self):
        """Einstellungen, die für alle Bauteile gemeinsam gelten."""
        if self.faces_actor is None:
            return
        show_faces = self.display_mode in ("schattiert", "schattiert_kanten")
        show_edges = self.display_mode in ("schattiert_kanten", "drahtgitter")
        self.faces_actor.SetVisibility(show_faces)
        self.faces_actor.SetPickable(show_faces)
        self.edges_actor.SetVisibility(show_edges and not self._interacting_hidden_edges)
        self.faces_actor.GetProperty().SetOpacity(0.35 if self.transparent else 1.0)
        if show_faces:
            ec = EDGE_COLOR
        else:
            ec = (0.1, 0.1, 0.12) if self.background == "hell" else (0.85, 0.88, 0.92)
        self.edges_actor.GetProperty().SetColor(*ec)

    def _style_part(self, p: PartActors):
        fa, ea = self.face_attrs, self.edge_attrs
        fa.SetBlockVisibility(p.faces, p.visible)
        fa.SetBlockPickability(p.faces, p.visible)
        ea.SetBlockVisibility(p.edges, p.visible)
        if p.selected:
            fa.SetBlockColor(p.faces, HIGHLIGHT)
            fa.SetBlockScalarVisibility(p.faces, False)
            ea.SetBlockColor(p.edges, HIGHLIGHT)
        else:
            fa.SetBlockColor(p.faces, p.node.color)
            # Flächenfarben aus der Datei nur für Bauteile, die welche haben
            fa.SetBlockScalarVisibility(p.faces, p.has_colors)
            ea.RemoveBlockColor(p.edges)

    def _attrs_changed(self):
        self.face_attrs.Modified()
        self.edge_attrs.Modified()
        if self.faces_actor is not None:
            self.faces_actor.GetMapper().Modified()
            self.edges_actor.GetMapper().Modified()

    def set_background(self, name: str):
        self.background = name
        bottom, top = BACKGROUNDS.get(name, BACKGROUNDS["dunkel"])
        self.renderer.SetBackground(*bottom)
        self.renderer.SetBackground2(*top)
        self._apply_display()
        self.render()

    def set_axes_visible(self, on: bool):
        self._axes_visible = on
        if not self._ready:
            return
        self.axes_widget.SetEnabled(1 if on else 0)
        if on:
            self.axes_widget.InteractiveOff()
        self.render()

    def set_parallel(self, on: bool):
        self.camera.SetParallelProjection(on)
        self.fit()

    def set_part_visible(self, p: PartActors, on: bool, render: bool = True):
        if p.visible == on:
            return
        p.visible = on
        self._style_part(p)
        self._attrs_changed()
        if render:
            self.render()

    def set_parts_visible(self, changes: list):
        """Mehrere Bauteile auf einmal ein-/ausblenden: [(part, sichtbar), …]."""
        changed = False
        for p, on in changes:
            if p.visible != on:
                p.visible = on
                self._style_part(p)
                changed = True
        if changed:
            self._attrs_changed()
            self.render()

    def set_selection(self, parts: list):
        sel = {id(p) for p in parts}
        changed = False
        for p in self.parts:
            was = p.selected
            p.selected = id(p) in sel
            if was != p.selected:
                self._style_part(p)
                changed = True
        if changed:
            self._attrs_changed()
        self.render()

    # -- Kamera --------------------------------------------------------------
    def visible_bounds(self, parts: Optional[list] = None):
        bounds = [math.inf, -math.inf, math.inf, -math.inf, math.inf, -math.inf]
        for p in parts if parts is not None else self.parts:
            if not p.visible:
                continue
            b = p.bounds
            if b[0] > b[1]:
                continue
            for i in range(0, 6, 2):
                bounds[i] = min(bounds[i], b[i])
                bounds[i + 1] = max(bounds[i + 1], b[i + 1])
        if bounds[0] > bounds[1]:
            return None
        return bounds

    def fit(self, parts: Optional[list] = None):
        b = self.visible_bounds(parts)
        if b is None:
            self.renderer.ResetCamera()
        else:
            self.renderer.ResetCamera(b)
            if self.camera.GetParallelProjection():
                self.camera.SetParallelScale(self.camera.GetParallelScale() * 0.92)
            else:
                self.camera.Zoom(1.08)
        self.renderer.ResetCameraClippingRange()
        self.render()

    def set_view(self, name: str, fit: bool = True):
        direction, up = VIEWS[name]
        focal = self.camera.GetFocalPoint()
        b = self.visible_bounds()
        if b is not None:
            focal = ((b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2)
        dist = max(self.camera.GetDistance(), 1.0)
        n = math.sqrt(sum(d * d for d in direction))
        self.camera.SetFocalPoint(*focal)
        self.camera.SetPosition(*(focal[i] + direction[i] / n * dist for i in range(3)))
        self.camera.SetViewUp(*up)
        if fit:
            self.fit()
        else:
            self.renderer.ResetCameraClippingRange()
            self.render()

    def rotate_view(self, degrees: float):
        self.camera.Roll(degrees)
        self.render()

    def apply_spacemouse(self, tx, ty, tz, rx, ry, rz, dt: float = 1 / 60):
        """Bewegung einer 3Dconnexion-SpaceMouse (Werte -1 … 1).

        dt = Zeit seit dem letzten Bild in Sekunden: die Geschwindigkeit bleibt
        gleich, egal wie schnell gezeichnet wird.
        """
        cam = self.camera
        dt = max(0.004, min(dt, 0.1))
        b = self.visible_bounds() or (0, 1, 0, 1, 0, 1)
        size = math.sqrt((b[1] - b[0]) ** 2 + (b[3] - b[2]) ** 2 + (b[5] - b[4]) ** 2) or 1.0
        # Verschieben in Bildschirmebene (volle Auslenkung ≈ 2,4 Bildhälften pro Sekunde)
        pos = list(cam.GetPosition())
        foc = list(cam.GetFocalPoint())
        up = cam.GetViewUp()
        dop = cam.GetDirectionOfProjection()
        right = (
            dop[1] * up[2] - dop[2] * up[1],
            dop[2] * up[0] - dop[0] * up[2],
            dop[0] * up[1] - dop[1] * up[0],
        )
        scale = (cam.GetParallelScale() if cam.GetParallelProjection() else cam.GetDistance() * 0.4) * 2.4 * dt
        move = [right[i] * tx * scale + up[i] * (-tz) * scale for i in range(3)]
        cam.SetPosition(*(pos[i] + move[i] for i in range(3)))
        cam.SetFocalPoint(*(foc[i] + move[i] for i in range(3)))
        # Zoom (nach vorne/hinten schieben)
        if abs(ty) > 0:
            factor = math.exp(-ty * 3.0 * dt)
            if cam.GetParallelProjection():
                cam.SetParallelScale(max(cam.GetParallelScale() / factor, size * 1e-4))
            else:
                cam.Dolly(factor)
        # Drehen um den Mittelpunkt (volle Auslenkung ≈ 150° pro Sekunde)
        deg = 150.0 * dt
        cam.Elevation(rx * deg)
        cam.Azimuth(-rz * deg)
        cam.Roll(ry * deg)
        cam.OrthogonalizeViewUp()
        self.renderer.ResetCameraClippingRange()
        self.render()

    # -- Auswahl & Messen ----------------------------------------------------
    def _pick(self, x: int, y: int):
        """Liefert (Bauteil, Weltkoordinate) unter dem Mauszeiger."""
        if self._render_scale is not None:
            # Koordinaten stammen aus der reduzierten Auflösung
            f = self.devicePixelRatioF() / self._render_scale
            x, y = int(x * f), int(y * f)
            self.end_interaction()
        if self.faces_actor is None or not self.faces_actor.GetVisibility():
            return None, None
        # Strahlauswahl prüft zuerst die Umrissboxen der Bauteile – wenige ms auch bei
        # tausenden Teilen (schneller als die Hardware-Auswahl, die mehrfach rendert)
        if self.picker.Pick(x, y, 0, self.renderer) and self.picker.GetActor() is self.faces_actor:
            return self._part_by_flat.get(self.picker.GetFlatBlockIndex()), self.picker.GetPickPosition()
        return None, None

    def handle_click(self, x: int, y: int):
        part, pos = self._pick(x, y)
        if self.measure_mode:
            if part is not None and pos is not None:
                self._add_measure_point(pos)
            return
        self.partPicked.emit(part.node if part else None)

    # -- Schnelle Darstellung während der Bewegung ---------------------------
    def lod_active(self) -> bool:
        """Bei großen Modellen werden die Kanten während der Bewegung ausgeblendet."""
        return self.lod_enabled and (self._edge_cells > 150_000 or len(self.parts) > 1500)

    def begin_interaction(self):
        self._lod_timer.stop()
        if self.lod_active() and not self._interacting_hidden_edges:
            self._interacting_hidden_edges = True
            self.renderer.SetUseFXAA(False)
            self._apply_display()
        # Offscreen auf Retina: während der Bewegung in halber Auflösung rendern
        if OFFSCREEN and self._render_scale is None and self.devicePixelRatioF() > 1.25:
            self._render_scale = 1.0
            self._resize_offscreen()

    def end_interaction(self, delay_ms: int = 0):
        if delay_ms:
            self._lod_timer.start(delay_ms)
            return
        self._lod_timer.stop()
        changed = False
        if self._interacting_hidden_edges:
            self._interacting_hidden_edges = False
            self.renderer.SetUseFXAA(True)
            self._apply_display()
            changed = True
        if self._render_scale is not None:
            self._render_scale = None
            self._resize_offscreen()
            changed = True
        if changed:
            self.render()

    def set_measure_mode(self, on: bool):
        self.measure_mode = on
        self.measure_points = []
        if on:
            self.measureStatus.emit("Messen: ersten Punkt auf dem Modell anklicken")
        else:
            self.measureStatus.emit("")

    def _model_size(self) -> float:
        b = self.visible_bounds() or (0, 1, 0, 1, 0, 1)
        return math.sqrt((b[1] - b[0]) ** 2 + (b[3] - b[2]) ** 2 + (b[5] - b[4]) ** 2) or 1.0

    def _add_measure_point(self, pt):
        size = self._model_size()
        sphere = vtkSphereSource()
        sphere.SetCenter(*pt)
        sphere.SetRadius(size * 0.004)
        sphere.SetThetaResolution(16)
        sphere.SetPhiResolution(16)
        m = vtkPolyDataMapper()
        m.SetInputConnection(sphere.GetOutputPort())
        a = vtkActor()
        a.SetMapper(m)
        a.GetProperty().SetColor(*MEASURE_COLOR)
        a.GetProperty().LightingOff()
        a.PickableOff()
        self.renderer.AddActor(a)
        self.measure_actors.append(a)
        self.measure_points.append(pt)

        if len(self.measure_points) == 1:
            self.measureStatus.emit("Messen: zweiten Punkt anklicken")
        else:
            p1, p2 = self.measure_points[-2], self.measure_points[-1]
            d = (p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2])
            dist = math.sqrt(sum(v * v for v in d))
            line = vtkLineSource()
            line.SetPoint1(*p1)
            line.SetPoint2(*p2)
            lm = vtkPolyDataMapper()
            lm.SetInputConnection(line.GetOutputPort())
            la = vtkActor()
            la.SetMapper(lm)
            la.GetProperty().SetColor(*MEASURE_COLOR)
            la.GetProperty().SetLineWidth(2.5)
            la.GetProperty().LightingOff()
            la.PickableOff()
            text = vtkBillboardTextActor3D()
            text.SetInput(f"{dist:.3f} mm")
            text.SetPosition(*((p1[i] + p2[i]) / 2 for i in range(3)))
            tp = text.GetTextProperty()
            tp.SetFontSize(18)
            tp.SetBold(True)
            tp.SetColor(*MEASURE_COLOR)
            tp.SetBackgroundColor(0.05, 0.05, 0.07)
            tp.SetBackgroundOpacity(0.75)
            tp.SetJustificationToCentered()
            text.PickableOff()
            self.renderer.AddActor(la)
            self.renderer.AddActor(text)
            self.measure_actors += [la, text]
            self.measure_points = []
            self.measured.emit(dist, d)
            self.measureStatus.emit("Messen: nächsten ersten Punkt anklicken (Esc beendet)")
        self.render()

    def clear_measurements(self):
        for a in self.measure_actors:
            self.renderer.RemoveActor(a)
        self.measure_actors.clear()
        self.measure_points = []
        self.render()

    # -- Export --------------------------------------------------------------
    def save_screenshot(self, path: str, scale: int = 2):
        self.render()
        w2i = vtkWindowToImageFilter()
        w2i.SetInput(self.render_window)
        w2i.SetScale(scale)
        w2i.SetInputBufferTypeToRGB()
        w2i.ReadFrontBufferOff()
        w2i.Update()
        writer = vtkPNGWriter()
        writer.SetFileName(path)
        writer.SetInputConnection(w2i.GetOutputPort())
        writer.Write()
        self.render()

    def render(self):
        """Neuzeichnen – erst wenn das Fenster bereit ist (wichtig für macOS)."""
        if not self._ready:
            return
        if OFFSCREEN:
            self.update()
            return
        try:
            self.render_window.Render()
        except Exception:  # noqa: BLE001
            log.exception("Rendern fehlgeschlagen")

    def keyPressEvent(self, ev):  # noqa: N802
        if ev.key() == Qt.Key_Escape and self.measure_mode:
            self.set_measure_mode(False)
            self.measureStatus.emit("__ende__")
            return
        # VTK-Standardtasten (w, s, 3, q, e …) nicht weiterleiten
        ev.ignore()

    def keyReleaseEvent(self, ev):  # noqa: N802
        ev.ignore()

