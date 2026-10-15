"""Einlesen von CAD-Dateien (STEP, IGES, BREP, STL) und Umwandlung in VTK-Netze.

Die Baugruppenstruktur, Bauteilnamen, Farben (auch einzelner Flächen) und die
Einbaulagen werden aus dem XCAF-Dokument von OpenCascade übernommen.
Gleiche Bauteile, die mehrfach verbaut sind, werden nur einmal vernetzt.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import vtkmodules.vtkCommonCore as vtk_core
import vtkmodules.vtkCommonDataModel as vtk_dm
from vtkmodules.vtkFiltersCore import vtkPolyDataNormals
from vtkmodules.vtkIOGeometry import vtkSTLReader

from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.Bnd import Bnd_Box
from OCP.GCPnts import GCPnts_TangentialDeflection
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.IGESCAFControl import IGESCAFControl_Reader
from OCP.Quantity import Quantity_Color
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDF import TDF_Label, TDF_Tool
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.XCAFDoc import XCAFDoc_ColorTool, XCAFDoc_ColorType, XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
from OCP.collections import Sequence_TDF_Label

SUPPORTED_EXT = (".step", ".stp", ".iges", ".igs", ".brep", ".brp", ".stl")
DEFAULT_COLOR = (0.72, 0.74, 0.78)

COLOR_TYPES = (
    XCAFDoc_ColorType.XCAFDoc_ColorSurf,
    XCAFDoc_ColorType.XCAFDoc_ColorGen,
)


# ---------------------------------------------------------------------------
# Datenstrukturen
# ---------------------------------------------------------------------------

IDENTITY = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


@dataclass
class MeshData:
    """Vernetzung eines Bauteils (im eigenen Koordinatensystem)."""

    faces: vtk_dm.vtkPolyData
    edges: vtk_dm.vtkPolyData
    has_face_colors: bool
    triangles: int


@dataclass
class ModelNode:
    """Knoten im Modellbaum (Baugruppe oder Bauteil)."""

    name: str
    is_assembly: bool = False
    children: list = field(default_factory=list)
    mesh_key: Optional[str] = None
    matrix: tuple = IDENTITY  # 4x4, zeilenweise
    color: tuple = DEFAULT_COLOR

    def leaf_count(self) -> int:
        if not self.children:
            return 1 if self.mesh_key else 0
        return sum(c.leaf_count() for c in self.children)


@dataclass
class LoadedModel:
    path: str
    root: ModelNode
    meshes: dict
    bounds: tuple  # xmin, xmax, ymin, ymax, zmin, zmax
    triangles: int
    parts: int
    unique_parts: int


class LoadError(Exception):
    pass


ProgressCb = Callable[[int, str], None]


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _fix_text(text: str) -> str:
    """Repariert typische UTF-8/Latin-1-Verwechslungen (z. B. 'WÃ¼rfel')."""
    if any(ch in text for ch in "ÃÂ"):
        try:
            return text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def _label_name(label: TDF_Label) -> str:
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        try:
            name = _fix_text(attr.Get().ToExtString()).strip()
        except Exception:  # noqa: BLE001
            return ""
        # automatisch erzeugte Instanznamen ausblenden
        if name.startswith("=>[") or _AUTO_NAME.fullmatch(name):
            return ""
        return name
    return ""


_AUTO_NAME = re.compile(r"(NAUO|SOLID|COMPOUND|SHELL)\d*", re.IGNORECASE)


def _label_color(label: TDF_Label) -> Optional[tuple]:
    col = Quantity_Color()
    for ctype in COLOR_TYPES:
        try:
            if XCAFDoc_ColorTool.GetColor_s(label, ctype, col):
                return (col.Red(), col.Green(), col.Blue())
        except Exception:  # noqa: BLE001
            return None
    return None


def _label_entry(label: TDF_Label) -> str:
    try:
        from OCP.TCollection import TCollection_AsciiString

        s = TCollection_AsciiString()
        TDF_Tool.Entry_s(label, s)
        return s.ToCString()
    except Exception:  # noqa: BLE001
        return str(id(label))


def _matrix_from_location(loc: TopLoc_Location) -> tuple:
    trsf = loc.Transformation()
    vals = []
    for r in range(1, 4):
        for c in range(1, 5):
            vals.append(trsf.Value(r, c))
    vals.extend([0.0, 0.0, 0.0, 1.0])
    return tuple(vals)


def _mat_mul(a: tuple, b: tuple) -> tuple:
    out = []
    for r in range(4):
        for c in range(4):
            out.append(sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)))
    return tuple(out)


def _shape_bounds(shape: TopoDS_Shape) -> Optional[tuple]:
    box = Bnd_Box()
    try:
        BRepBndLib.Add_s(shape, box, True)
    except Exception:  # noqa: BLE001
        return None
    if box.IsVoid():
        return None
    pmin, pmax = box.CornerMin(), box.CornerMax()
    return (pmin.X(), pmax.X(), pmin.Y(), pmax.Y(), pmin.Z(), pmax.Z())


def _count_solids(shape: TopoDS_Shape) -> list:
    solids = []
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        solids.append(exp.Current())
        exp.Next()
    return solids


# ---------------------------------------------------------------------------
# Vernetzung
# ---------------------------------------------------------------------------

class Mesher:
    def __init__(self, deflection: float, angular: float = 0.35):
        self.deflection = max(deflection, 1e-6)
        self.angular = angular

    def mesh(self, shape: TopoDS_Shape, face_colors: dict) -> MeshData:
        BRepMesh_IncrementalMesh(shape, self.deflection, False, self.angular, True)

        points = vtk_core.vtkPoints()
        points.SetDataTypeToDouble()
        polys = vtk_dm.vtkCellArray()
        colors = vtk_core.vtkUnsignedCharArray()
        colors.SetNumberOfComponents(3)
        colors.SetName("Farben")
        use_colors = bool(face_colors)

        edge_pts = vtk_core.vtkPoints()
        edge_pts.SetDataTypeToDouble()
        edge_lines = vtk_dm.vtkCellArray()
        seen_edges = set()
        tri_count = 0

        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            face = TopoDS.Face(exp.Current())
            exp.Next()
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is None:
                continue
            trsf = loc.Transformation()
            identity = loc.IsIdentity()
            offset = points.GetNumberOfPoints()
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i)
                if not identity:
                    p = p.Transformed(trsf)
                points.InsertNextPoint(p.X(), p.Y(), p.Z())

            reversed_face = face.Orientation() == TopAbs_REVERSED
            rgb = None
            if use_colors:
                c = face_colors.get(hash(face.Oriented(TopAbs_FORWARD)))
                if c is None:
                    c = face_colors.get("__default__", DEFAULT_COLOR)
                rgb = tuple(int(max(0, min(1, v)) * 255) for v in c)

            for i in range(1, tri.NbTriangles() + 1):
                n1, n2, n3 = tri.Triangle(i).Get()
                if reversed_face:
                    n2, n3 = n3, n2
                polys.InsertNextCell(3)
                polys.InsertCellPoint(offset + n1 - 1)
                polys.InsertCellPoint(offset + n2 - 1)
                polys.InsertCellPoint(offset + n3 - 1)
                if rgb:
                    colors.InsertNextTuple3(*rgb)
            tri_count += tri.NbTriangles()

            # Kanten der Fläche (doppelte Kanten werden übersprungen)
            eexp = TopExp_Explorer(face, TopAbs_EDGE)
            while eexp.More():
                edge = TopoDS.Edge(eexp.Current())
                eexp.Next()
                key = hash(edge.Oriented(TopAbs_FORWARD))
                if key in seen_edges:
                    continue
                # Nahtkanten (z. B. bei Zylindern) nicht als Kante zeichnen
                if BRep_Tool.IsClosed_s(edge, face):
                    seen_edges.add(key)
                    continue
                seen_edges.add(key)
                self._add_edge(edge, tri, loc, edge_pts, edge_lines)

        faces = vtk_dm.vtkPolyData()
        faces.SetPoints(points)
        faces.SetPolys(polys)
        if use_colors and colors.GetNumberOfTuples() == polys.GetNumberOfCells():
            faces.GetCellData().SetScalars(colors)

        normals = vtkPolyDataNormals()
        normals.SetInputData(faces)
        normals.SetFeatureAngle(35.0)
        normals.SplittingOn()
        normals.ConsistencyOff()
        normals.AutoOrientNormalsOff()
        normals.Update()
        out = vtk_dm.vtkPolyData()
        out.ShallowCopy(normals.GetOutput())

        edges = vtk_dm.vtkPolyData()
        edges.SetPoints(edge_pts)
        edges.SetLines(edge_lines)
        return MeshData(out, edges, use_colors, tri_count)

    def _add_edge(self, edge, tri, loc, edge_pts, edge_lines):
        if BRep_Tool.Degenerated_s(edge):
            return
        coords = []
        try:
            poly = BRep_Tool.PolygonOnTriangulation_s(edge, tri, loc)
        except Exception:  # noqa: BLE001
            poly = None
        if poly is not None:
            trsf = loc.Transformation()
            identity = loc.IsIdentity()
            nodes = poly.Nodes()
            for i in range(nodes.Lower(), nodes.Upper() + 1):
                p = tri.Node(nodes.Value(i))
                if not identity:
                    p = p.Transformed(trsf)
                coords.append((p.X(), p.Y(), p.Z()))
        else:
            try:
                curve = BRepAdaptor_Curve(edge)
                disc = GCPnts_TangentialDeflection(curve, self.angular, self.deflection)
                for i in range(1, disc.NbPoints() + 1):
                    p = disc.Value(i)
                    coords.append((p.X(), p.Y(), p.Z()))
            except Exception:  # noqa: BLE001
                return
        if len(coords) < 2:
            return
        edge_lines.InsertNextCell(len(coords))
        for c in coords:
            edge_lines.InsertCellPoint(edge_pts.InsertNextPoint(*c))


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class ModelLoader:
    """Liest eine Datei ein und liefert ein LoadedModel."""

    def __init__(self, quality: str = "normal", progress: Optional[ProgressCb] = None):
        self.quality = quality
        self.progress = progress or (lambda p, t: None)
        self.meshes: dict = {}
        self._mesher: Optional[Mesher] = None
        self._total_parts = 1
        self._done_parts = 0

    # -- öffentlich ---------------------------------------------------------
    def load(self, path: str) -> LoadedModel:
        if not os.path.isfile(path):
            raise LoadError(f"Datei nicht gefunden:\n{path}")
        ext = os.path.splitext(path)[1].lower()
        self.progress(2, "Datei wird gelesen …")
        if ext in (".stl",):
            root = self._load_stl(path)
        elif ext in (".brep", ".brp"):
            root = self._load_brep(path)
        elif ext in (".step", ".stp", ".iges", ".igs"):
            root = self._load_xcaf(path, ext)
        else:
            raise LoadError(f"Dateiformat '{ext}' wird nicht unterstützt.")

        bounds = self._compute_bounds(root)
        tris = sum(m.triangles for m in self.meshes.values())
        self.progress(100, "Fertig")
        return LoadedModel(
            path=path,
            root=root,
            meshes=self.meshes,
            bounds=bounds,
            triangles=tris,
            parts=root.leaf_count(),
            unique_parts=len(self.meshes),
        )

    # -- Formate -------------------------------------------------------------
    def _load_stl(self, path: str) -> ModelNode:
        reader = vtkSTLReader()
        reader.SetFileName(path)
        reader.Update()
        poly = reader.GetOutput()
        if poly.GetNumberOfPoints() == 0:
            raise LoadError("Die STL-Datei enthält keine Dreiecke.")
        normals = vtkPolyDataNormals()
        normals.SetInputData(poly)
        normals.SetFeatureAngle(35.0)
        normals.Update()
        faces = vtk_dm.vtkPolyData()
        faces.ShallowCopy(normals.GetOutput())
        from vtkmodules.vtkFiltersCore import vtkFeatureEdges

        fe = vtkFeatureEdges()
        fe.SetInputData(poly)
        fe.BoundaryEdgesOn()
        fe.FeatureEdgesOn()
        fe.SetFeatureAngle(35.0)
        fe.ManifoldEdgesOff()
        fe.NonManifoldEdgesOff()
        fe.Update()
        edges = vtk_dm.vtkPolyData()
        edges.ShallowCopy(fe.GetOutput())
        self.meshes["stl"] = MeshData(faces, edges, False, poly.GetNumberOfCells())
        name = os.path.splitext(os.path.basename(path))[0]
        return ModelNode(name=name, mesh_key="stl")

    def _load_brep(self, path: str) -> ModelNode:
        shape = TopoDS_Shape()
        builder = BRep_Builder()
        ok = BRepTools.Read_s(shape, path, builder)
        if not ok or shape.IsNull():
            raise LoadError("BREP-Datei konnte nicht gelesen werden.")
        self._setup_mesher([shape])
        name = os.path.splitext(os.path.basename(path))[0]
        node = self._make_part_node(name, shape, {}, DEFAULT_COLOR, "brep")
        return node

    def _load_xcaf(self, path: str, ext: str) -> ModelNode:
        doc = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
        if ext in (".step", ".stp"):
            reader = STEPCAFControl_Reader()
        else:
            reader = IGESCAFControl_Reader()
        reader.SetColorMode(True)
        reader.SetNameMode(True)
        try:
            reader.SetLayerMode(True)
        except Exception:  # noqa: BLE001
            pass
        status = reader.ReadFile(path)
        if status != IFSelect_ReturnStatus.IFSelect_RetDone:
            raise LoadError("Die Datei konnte nicht gelesen werden (ungültig oder beschädigt).")
        self.progress(25, "Geometrie wird übertragen …")
        if not reader.Transfer(doc):
            raise LoadError("Die Geometrie konnte nicht übertragen werden.")

        shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        self._shape_tool = shape_tool
        free = Sequence_TDF_Label()
        shape_tool.GetFreeShapes(free)
        if free.Length() == 0:
            raise LoadError("Die Datei enthält keine Geometrie.")

        top_shapes = [XCAFDoc_ShapeTool.GetShape_s(free.Value(i)) for i in range(1, free.Length() + 1)]
        self._setup_mesher(top_shapes)
        self._total_parts = max(1, self._count_parts(free))
        self._done_parts = 0
        self.progress(35, "Bauteile werden vernetzt …")

        file_name = os.path.splitext(os.path.basename(path))[0]
        children = []
        for i in range(1, free.Length() + 1):
            label = free.Value(i)
            hint = None if free.Length() > 1 else (_label_name(label) or file_name)
            node = self._walk(label, IDENTITY, DEFAULT_COLOR, hint)
            if node is not None:
                children.append(node)

        if len(children) == 1:
            root = children[0]
            if not root.name:
                root.name = file_name
        else:
            root = ModelNode(name=file_name, is_assembly=True, children=children)
        return root

    # -- Baugruppen-Durchlauf -------------------------------------------------
    def _count_parts(self, labels) -> int:
        count = 0
        for i in range(1, labels.Length() + 1):
            label = labels.Value(i)
            if XCAFDoc_ShapeTool.IsReference_s(label):
                ref = TDF_Label()
                XCAFDoc_ShapeTool.GetReferredShape_s(label, ref)
                label = ref
            if XCAFDoc_ShapeTool.IsAssembly_s(label):
                comps = Sequence_TDF_Label()
                XCAFDoc_ShapeTool.GetComponents_s(label, comps, False)
                count += self._count_parts(comps)
            else:
                count += 1
        return count

    def _walk(self, label: TDF_Label, matrix: tuple, inherited: tuple, name_hint: Optional[str]):
        name = name_hint or _label_name(label)
        color = _label_color(label) or inherited

        if XCAFDoc_ShapeTool.IsReference_s(label):
            ref = TDF_Label()
            XCAFDoc_ShapeTool.GetReferredShape_s(label, ref)
            loc = XCAFDoc_ShapeTool.GetLocation_s(label)
            m = _mat_mul(matrix, _matrix_from_location(loc))
            ref_name = _label_name(ref)
            ref_color = _label_color(label) or _label_color(ref) or inherited
            return self._walk(ref, m, ref_color, name or ref_name)

        if XCAFDoc_ShapeTool.IsAssembly_s(label):
            node = ModelNode(name=name or "Baugruppe", is_assembly=True, matrix=IDENTITY, color=color)
            comps = Sequence_TDF_Label()
            XCAFDoc_ShapeTool.GetComponents_s(label, comps, False)
            for i in range(1, comps.Length() + 1):
                child = self._walk(comps.Value(i), matrix, color, None)
                if child is not None:
                    node.children.append(child)
            return node if node.children else None

        shape = XCAFDoc_ShapeTool.GetShape_s(label)
        if shape.IsNull():
            return None
        face_colors = self._face_colors(label)
        key = _label_entry(label)
        node = self._make_part_node(name or "Bauteil", shape, face_colors, color, key)
        node.matrix = _mat_mul(matrix, node.matrix) if node.mesh_key else matrix
        if node.children:
            for c in node.children:
                c.matrix = matrix
        self._done_parts += 1
        pct = 35 + int(60 * self._done_parts / self._total_parts)
        self.progress(min(pct, 95), f"Vernetze Bauteil {self._done_parts} von {self._total_parts} …")
        return node

    def _face_colors(self, label: TDF_Label) -> dict:
        subs = Sequence_TDF_Label()
        try:
            XCAFDoc_ShapeTool.GetSubShapes_s(label, subs)
        except Exception:  # noqa: BLE001
            return {}
        result = {}
        for i in range(1, subs.Length() + 1):
            sub = subs.Value(i)
            col = _label_color(sub)
            if col is None:
                continue
            sub_shape = XCAFDoc_ShapeTool.GetShape_s(sub)
            exp = TopExp_Explorer(sub_shape, TopAbs_FACE)
            while exp.More():
                result[hash(exp.Current().Oriented(TopAbs_FORWARD))] = col
                exp.Next()
        return result

    def _make_part_node(self, name, shape, face_colors, color, key) -> ModelNode:
        solids = _count_solids(shape)
        if len(solids) > 1 and not face_colors:
            # Mehrkörper-Bauteil: jeden Körper einzeln im Baum zeigen
            node = ModelNode(name=name, is_assembly=True, color=color)
            for idx, solid in enumerate(solids, start=1):
                skey = f"{key}#{idx}"
                if skey not in self.meshes:
                    self.meshes[skey] = self._mesher.mesh(solid, {})
                node.children.append(ModelNode(name=f"Körper {idx}", mesh_key=skey, color=color))
            return node
        if key not in self.meshes:
            fc = dict(face_colors)
            if fc:
                fc["__default__"] = color
            self.meshes[key] = self._mesher.mesh(shape, fc)
        return ModelNode(name=name, mesh_key=key, color=color)

    # -- Sonstiges -----------------------------------------------------------
    def _setup_mesher(self, shapes):
        box = None
        for s in shapes:
            b = _shape_bounds(s)
            if b is None:
                continue
            box = b if box is None else (
                min(box[0], b[0]), max(box[1], b[1]),
                min(box[2], b[2]), max(box[3], b[3]),
                min(box[4], b[4]), max(box[5], b[5]),
            )
        diag = 100.0
        if box:
            diag = math.sqrt((box[1] - box[0]) ** 2 + (box[3] - box[2]) ** 2 + (box[5] - box[4]) ** 2) or 100.0
        factor = {"grob": 0.004, "normal": 0.0012, "fein": 0.0004}.get(self.quality, 0.0012)
        angular = {"grob": 0.6, "normal": 0.35, "fein": 0.2}.get(self.quality, 0.35)
        self._mesher = Mesher(diag * factor, angular)

    def _compute_bounds(self, root: ModelNode) -> tuple:
        bounds = [math.inf, -math.inf, math.inf, -math.inf, math.inf, -math.inf]

        def visit(node: ModelNode):
            if node.mesh_key:
                mesh = self.meshes[node.mesh_key]
                b = mesh.faces.GetBounds()
                if b[0] <= b[1]:
                    m = node.matrix
                    for x in (b[0], b[1]):
                        for y in (b[2], b[3]):
                            for z in (b[4], b[5]):
                                px = m[0] * x + m[1] * y + m[2] * z + m[3]
                                py = m[4] * x + m[5] * y + m[6] * z + m[7]
                                pz = m[8] * x + m[9] * y + m[10] * z + m[11]
                                bounds[0] = min(bounds[0], px); bounds[1] = max(bounds[1], px)
                                bounds[2] = min(bounds[2], py); bounds[3] = max(bounds[3], py)
                                bounds[4] = min(bounds[4], pz); bounds[5] = max(bounds[5], pz)
            for c in node.children:
                visit(c)

        visit(root)
        if bounds[0] > bounds[1]:
            return (0, 1, 0, 1, 0, 1)
        return tuple(bounds)
