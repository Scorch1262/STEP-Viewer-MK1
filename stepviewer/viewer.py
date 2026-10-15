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

from PySide6.QtCore import Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402
from vtkmodules.vtkCommonCore import vtkUnsignedCharArray  # noqa: E402
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor  # noqa: E402
from vtkmodules.vtkCommonMath import vtkMatrix4x4  # noqa: E402
from vtkmodules.vtkFiltersSources import vtkLineSource, vtkSphereSource  # noqa: E402
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera  # noqa: E402
from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget  # noqa: E402
from vtkmodules.vtkIOImage import vtkPNGWriter  # noqa: E402
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor  # noqa: E402
from vtkmodules.vtkRenderingCore import (  # noqa: E402
    vtkRenderWindow,
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


class PartActors:
    def __init__(self, node: ModelNode, faces: vtkActor, edges: vtkActor, has_colors: bool):
        self.node = node
        self.faces = faces
        self.edges = edges
        self.has_colors = has_colors
        self.visible = True
        self.selected = False


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
        if OFFSCREEN:
            # Renderwünsche des Interaktors (Drehen, Zoomen …) als Qt-Neuzeichnen ausführen
            self._Iren.AddObserver("RenderEvent", lambda *a: self.update())
        log.info("Darstellung: %s", RENDER_MODE)
        self.setFocusPolicy(Qt.StrongFocus)
        self.renderer = vtkRenderer()
        self.renderer.GradientBackgroundOn()
        self.renderer.SetUseFXAA(True)
        self.render_window = self.GetRenderWindow()
        self.render_window.AddRenderer(self.renderer)
        self.render_window.SetMultiSamples(0)

        self.style = _Style(self)
        self.style.SetDefaultRenderer(self.renderer)
        self.GetRenderWindow().GetInteractor().SetInteractorStyle(self.style)

        self.picker = vtkCellPicker()
        self.picker.SetTolerance(0.0005)

        self.parts: list[PartActors] = []
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

    def _resize_offscreen(self):
        scale = self.devicePixelRatioF()
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
            img.setDevicePixelRatio(self.devicePixelRatioF())
            p = QPainter(self)
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
        for p in self.parts:
            self.renderer.RemoveActor(p.faces)
            self.renderer.RemoveActor(p.edges)
        self.parts.clear()
        self.clear_measurements()
        self.model = None

    def set_model(self, model: LoadedModel) -> dict:
        """Erzeugt die Darstellung und liefert {id(node): PartActors}."""
        self.clear()
        self.model = model
        mapping = {}
        mappers = {}

        def visit(node: ModelNode):
            if node.mesh_key:
                mesh = model.meshes[node.mesh_key]
                if node.mesh_key not in mappers:
                    fm = vtkPolyDataMapper()
                    fm.SetInputData(mesh.faces)
                    fm.SetScalarModeToUseCellData()
                    fm.SetColorModeToDirectScalars()
                    fm.SetResolveCoincidentTopologyToPolygonOffset()
                    fm.SetRelativeCoincidentTopologyPolygonOffsetParameters(1.0, 1.0)
                    em = vtkPolyDataMapper()
                    em.SetInputData(mesh.edges)
                    em.ScalarVisibilityOff()
                    mappers[node.mesh_key] = (fm, em)
                fm, em = mappers[node.mesh_key]
                matrix = vtkMatrix4x4()
                matrix.DeepCopy(node.matrix)

                fa = vtkActor()
                fa.SetMapper(fm)
                fa.SetUserMatrix(matrix)
                prop = fa.GetProperty()
                prop.SetInterpolationToPhong()
                prop.SetAmbient(0.18)
                prop.SetDiffuse(0.78)
                prop.SetSpecular(0.25)
                prop.SetSpecularPower(30)
                prop.SetColor(*node.color)
                prop.BackfaceCullingOff()

                ea = vtkActor()
                ea.SetMapper(em)
                ea.SetUserMatrix(matrix)
                ea.GetProperty().SetColor(*EDGE_COLOR)
                ea.GetProperty().SetLineWidth(1.2)
                ea.GetProperty().LightingOff()
                ea.PickableOff()

                part = PartActors(node, fa, ea, mesh.has_face_colors)
                self.parts.append(part)
                mapping[id(node)] = part
                self.renderer.AddActor(fa)
                self.renderer.AddActor(ea)
            for c in node.children:
                visit(c)

        visit(model.root)
        self._apply_display()
        self.set_view("iso", fit=True)
        return mapping

    def _part_for_actor(self, actor) -> Optional[PartActors]:
        for p in self.parts:
            if p.faces is actor:
                return p
        return None

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
        for p in self.parts:
            self._style_part(p)

    def _style_part(self, p: PartActors):
        show_faces = self.display_mode in ("schattiert", "schattiert_kanten")
        show_edges = self.display_mode in ("schattiert_kanten", "drahtgitter")
        p.faces.SetVisibility(p.visible and show_faces)
        p.edges.SetVisibility(p.visible and show_edges)
        p.faces.SetPickable(p.visible and show_faces)
        fprop = p.faces.GetProperty()
        eprop = p.edges.GetProperty()
        if p.selected:
            fprop.SetColor(*HIGHLIGHT)
            fprop.SetAmbient(0.35)
            eprop.SetColor(*HIGHLIGHT)
            eprop.SetLineWidth(2.0)
        else:
            fprop.SetColor(*p.node.color)
            fprop.SetAmbient(0.18)
            eprop.SetColor(*(EDGE_COLOR if show_faces else (0.85, 0.88, 0.92) if self.background != "hell" else (0.1, 0.1, 0.12)))
            eprop.SetLineWidth(1.2)
        fprop.SetOpacity(0.35 if self.transparent else 1.0)
        # Flächenfarben aus der Datei nur ohne Hervorhebung nutzen
        self._set_scalar_visibility(p, p.has_colors and not p.selected)

    def _set_scalar_visibility(self, p: PartActors, on: bool):
        # Der Mapper wird von Instanzen geteilt. Für hervorgehobene Instanzen
        # bekommt der Actor einen eigenen Mapper ohne Skalare.
        mapper = p.faces.GetMapper()
        if on == bool(mapper.GetScalarVisibility()):
            return
        new = vtkPolyDataMapper()
        new.SetInputData(mapper.GetInput())
        new.SetScalarModeToUseCellData()
        new.SetColorModeToDirectScalars()
        new.SetResolveCoincidentTopologyToPolygonOffset()
        new.SetRelativeCoincidentTopologyPolygonOffsetParameters(1.0, 1.0)
        new.SetScalarVisibility(on)
        p.faces.SetMapper(new)

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

    def set_part_visible(self, p: PartActors, on: bool):
        p.visible = on
        self._style_part(p)

    def set_selection(self, parts: list):
        sel = {id(p) for p in parts}
        for p in self.parts:
            was = p.selected
            p.selected = id(p) in sel
            if was != p.selected:
                self._style_part(p)
        self.render()

    # -- Kamera --------------------------------------------------------------
    def visible_bounds(self, parts: Optional[list] = None):
        bounds = [math.inf, -math.inf, math.inf, -math.inf, math.inf, -math.inf]
        for p in parts if parts is not None else self.parts:
            if not p.visible:
                continue
            b = p.faces.GetBounds()
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

    def apply_spacemouse(self, tx, ty, tz, rx, ry, rz):
        """Bewegung einer 3Dconnexion-SpaceMouse (Werte grob -1 … 1)."""
        cam = self.camera
        b = self.visible_bounds() or (0, 1, 0, 1, 0, 1)
        size = math.sqrt((b[1] - b[0]) ** 2 + (b[3] - b[2]) ** 2 + (b[5] - b[4]) ** 2) or 1.0
        # Verschieben in Bildschirmebene
        pos = list(cam.GetPosition())
        foc = list(cam.GetFocalPoint())
        up = cam.GetViewUp()
        dop = cam.GetDirectionOfProjection()
        right = (
            dop[1] * up[2] - dop[2] * up[1],
            dop[2] * up[0] - dop[0] * up[2],
            dop[0] * up[1] - dop[1] * up[0],
        )
        scale = (cam.GetParallelScale() if cam.GetParallelProjection() else cam.GetDistance() * 0.4) * 0.04
        move = [right[i] * tx * scale + up[i] * (-tz) * scale for i in range(3)]
        cam.SetPosition(*(pos[i] + move[i] for i in range(3)))
        cam.SetFocalPoint(*(foc[i] + move[i] for i in range(3)))
        # Zoom (nach vorne/hinten schieben)
        if abs(ty) > 0:
            factor = 1.0 + max(-0.5, min(0.5, -ty * 0.05))
            if cam.GetParallelProjection():
                cam.SetParallelScale(max(cam.GetParallelScale() / factor, size * 1e-4))
            else:
                cam.Dolly(factor)
        # Drehen um den Mittelpunkt
        cam.Elevation(rx * 2.5)
        cam.Azimuth(-rz * 2.5)
        cam.Roll(ry * 2.5)
        cam.OrthogonalizeViewUp()
        self.renderer.ResetCameraClippingRange()
        self.render()

    # -- Auswahl & Messen ----------------------------------------------------
    def handle_click(self, x: int, y: int):
        hit = self.picker.Pick(x, y, 0, self.renderer)
        actor = self.picker.GetActor() if hit else None
        if self.measure_mode:
            if actor is None:
                return
            self._add_measure_point(self.picker.GetPickPosition())
            return
        part = self._part_for_actor(actor) if actor is not None else None
        self.partPicked.emit(part.node if part else None)

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

