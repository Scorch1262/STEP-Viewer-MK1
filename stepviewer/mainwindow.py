"""Hauptfenster des STEP-Viewer-MK1."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, ORG_NAME, VERSION, spacemouse
from .loader import SUPPORTED_EXT, LoadError, LoadedModel, ModelLoader, ModelNode
from .viewer import Viewport

FILE_FILTER = (
    "CAD-Dateien (*.step *.stp *.STEP *.STP *.iges *.igs *.IGES *.IGS *.brep *.brp *.stl *.STL);;"
    "STEP (*.step *.stp *.STEP *.STP);;IGES (*.iges *.igs *.IGES *.IGS);;"
    "BREP (*.brep *.brp);;STL (*.stl *.STL);;Alle Dateien (*)"
)
MAX_RECENT = 10
ROLE_NODE = Qt.UserRole + 1


class LoadWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object, float)
    failed = Signal(str)

    def __init__(self, path: str, quality: str):
        super().__init__()
        self.path = path
        self.quality = quality

    @Slot()
    def run(self):
        start = time.perf_counter()
        try:
            loader = ModelLoader(self.quality, progress=lambda p, t: self.progress.emit(p, t))
            model = loader.load(self.path)
        except LoadError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unerwarteter Fehler beim Laden:\n{exc}")
            return
        self.finished.emit(model, time.perf_counter() - start)


class MainWindow(QMainWindow):
    def __init__(self, icon: QIcon | None = None):
        super().__init__()
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.model: LoadedModel | None = None
        self.part_map: dict = {}
        self.item_for_node: dict = {}
        self._thread: QThread | None = None
        self._worker: LoadWorker | None = None
        self._updating_tree = False
        self._load_time = 0.0
        self.sm_thread: spacemouse.SpaceMouseThread | None = None

        self.setWindowTitle(f"{APP_NAME}  v{VERSION}")
        if icon is not None:
            self.setWindowIcon(icon)
        self.resize(1400, 860)
        self.setAcceptDrops(True)

        self.viewport = Viewport(self)
        self.setCentralWidget(self.viewport)
        self.viewport.partPicked.connect(self._on_part_picked)
        self.viewport.measured.connect(self._on_measured)
        self.viewport.measureStatus.connect(self._on_measure_status)

        self._build_dock()
        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_statusbar()
        self._restore_settings()
        self._update_info()

    # ------------------------------------------------------------------ UI
    def _build_dock(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Modellbaum"])
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        self.tree.itemDoubleClicked.connect(lambda *_: self._fit_selection())

        self.info = QLabel()
        self.info.setTextFormat(Qt.RichText)
        self.info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setObjectName("infoPanel")

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.info)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(splitter)

        self.dock = QDockWidget("Modell", self)
        self.dock.setObjectName("modelDock")
        self.dock.setWidget(container)
        self.dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.dock)
        self.resizeDocks([self.dock], [300], Qt.Horizontal)

    def _act(self, text, slot=None, shortcut=None, checkable=False, tip=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if checkable:
            a.setCheckable(True)
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        if slot is not None:
            a.triggered.connect(slot)
        return a

    def _build_actions(self):
        self.a_open = self._act("Öffnen …", self.open_dialog, QKeySequence.Open, tip="CAD-Datei öffnen (Strg+O)")
        self.a_reload = self._act("Neu laden", self.reload, "F5", tip="Datei neu einlesen (F5)")
        self.a_shot = self._act("Bild speichern …", self.save_screenshot, "Ctrl+Shift+S",
                                tip="Aktuelle Ansicht als PNG speichern")
        self.a_quit = self._act("Beenden", self.close, QKeySequence.Quit)

        self.a_fit = self._act("Einpassen", lambda: self.viewport.fit(), "F", tip="Alles einpassen (F)")
        self.view_actions = {}
        for key, text, sc in (
            ("iso", "Isometrisch", "0"), ("vorne", "Vorne", "1"), ("hinten", "Hinten", "Ctrl+1"),
            ("rechts", "Rechts", "3"), ("links", "Links", "Ctrl+3"), ("oben", "Oben", "7"),
            ("unten", "Unten", "Ctrl+7"),
        ):
            self.view_actions[key] = self._act(text, lambda _=False, k=key: self.viewport.set_view(k), sc,
                                               tip=f"Ansicht {text} ({sc})")
        self.a_roll_l = self._act("⟲ 90°", lambda: self.viewport.rotate_view(90), "Ctrl+Left",
                                  tip="Ansicht 90° drehen")
        self.a_roll_r = self._act("⟳ 90°", lambda: self.viewport.rotate_view(-90), "Ctrl+Right",
                                  tip="Ansicht 90° drehen")

        self.mode_group = QActionGroup(self)
        self.mode_actions = {}
        for key, text, sc in (
            ("schattiert_kanten", "Schattiert + Kanten", "Ctrl+E"),
            ("schattiert", "Schattiert", "Ctrl+R"),
            ("drahtgitter", "Kanten (Drahtgitter)", "Ctrl+W"),
        ):
            a = self._act(text, lambda _=False, k=key: self._set_mode(k), sc, checkable=True)
            self.mode_group.addAction(a)
            self.mode_actions[key] = a

        self.a_transp = self._act("Transparent", self._toggle_transparent, "T", checkable=True,
                                  tip="Modell transparent darstellen (T)")
        self.a_persp = self._act("Perspektive", self._toggle_perspective, "P", checkable=True,
                                 tip="Perspektivische Projektion (P)")
        self.a_axes = self._act("Achsenkreuz", self._toggle_axes, checkable=True)
        self.a_axes.setChecked(True)

        self.bg_group = QActionGroup(self)
        self.bg_actions = {}
        for key, text in (("dunkel", "Dunkel"), ("hell", "Hell"), ("creo", "Blau-Grau")):
            a = self._act(text, lambda _=False, k=key: self._set_background(k), checkable=True)
            self.bg_group.addAction(a)
            self.bg_actions[key] = a

        self.a_measure = self._act("Messen", self._toggle_measure, "M", checkable=True,
                                   tip="Abstand zwischen zwei Punkten messen (M)")
        self.a_clear_measure = self._act("Messungen löschen", self.viewport.clear_measurements)
        self.a_show_all = self._act("Alle anzeigen", self._show_all, "Ctrl+Shift+A")
        self.a_hide_sel = self._act("Auswahl ausblenden", self._hide_selection, "H")
        self.a_isolate = self._act("Nur Auswahl anzeigen", self._isolate_selection, "I")
        self.a_fit_sel = self._act("Auswahl einpassen", self._fit_selection, "Shift+F")

        self.a_spacemouse = self._act("SpaceMouse verwenden", self._toggle_spacemouse, checkable=True,
                                      tip="3Dconnexion SpaceMouse direkt auslesen (experimentell)")
        self.a_spacemouse.setEnabled(spacemouse.available())

        self.quality_group = QActionGroup(self)
        self.quality_actions = {}
        for key, text in (("grob", "Grob (schnell)"), ("normal", "Normal"), ("fein", "Fein (langsam)")):
            a = self._act(text, lambda _=False, k=key: self._set_quality(k), checkable=True)
            self.quality_group.addAction(a)
            self.quality_actions[key] = a

        # kurze Beschriftungen für die Werkzeugleiste
        for a, short in (
            (self.view_actions["iso"], "Iso"), (self.mode_actions["schattiert_kanten"], "Kanten+"),
            (self.mode_actions["drahtgitter"], "Draht"), (self.a_shot, "Bild"),
        ):
            a.setIconText(short)

        self.a_help = self._act("Bedienung", self._show_help, "F1")
        self.a_about = self._act(f"Über {APP_NAME}", self._show_about)

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&Datei")
        m.addAction(self.a_open)
        self.recent_menu = m.addMenu("Zuletzt geöffnet")
        m.addAction(self.a_reload)
        m.addSeparator()
        m.addAction(self.a_shot)
        m.addSeparator()
        m.addAction(self.a_quit)

        m = mb.addMenu("&Ansicht")
        m.addAction(self.a_fit)
        vm = m.addMenu("Standardansichten")
        for a in self.view_actions.values():
            vm.addAction(a)
        vm.addSeparator()
        vm.addAction(self.a_roll_l)
        vm.addAction(self.a_roll_r)
        m.addSeparator()
        for a in self.mode_actions.values():
            m.addAction(a)
        m.addAction(self.a_transp)
        m.addSeparator()
        m.addAction(self.a_persp)
        m.addAction(self.a_axes)
        bm = m.addMenu("Hintergrund")
        for a in self.bg_actions.values():
            bm.addAction(a)
        m.addSeparator()
        m.addAction(self.dock.toggleViewAction())

        m = mb.addMenu("&Modell")
        m.addAction(self.a_show_all)
        m.addAction(self.a_hide_sel)
        m.addAction(self.a_isolate)
        m.addAction(self.a_fit_sel)

        m = mb.addMenu("&Werkzeuge")
        m.addAction(self.a_measure)
        m.addAction(self.a_clear_measure)
        m.addSeparator()
        m.addAction(self.a_spacemouse)
        qm = m.addMenu("Darstellungsqualität")
        for a in self.quality_actions.values():
            qm.addAction(a)

        m = mb.addMenu("&Hilfe")
        m.addAction(self.a_help)
        m.addAction(self.a_about)

    def _build_toolbars(self):
        tb = QToolBar("Datei", self)
        tb.setObjectName("tbFile")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        tb.addAction(self.a_open)
        tb.addAction(self.a_reload)
        tb.addAction(self.a_shot)
        self.addToolBar(tb)

        tb = QToolBar("Ansicht", self)
        tb.setObjectName("tbView")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        tb.addAction(self.a_fit)
        tb.addSeparator()
        for a in self.view_actions.values():
            tb.addAction(a)
        tb.addSeparator()
        for a in self.mode_actions.values():
            tb.addAction(a)
        tb.addAction(self.a_transp)
        tb.addAction(self.a_persp)
        tb.addSeparator()
        tb.addAction(self.a_measure)
        self.addToolBar(tb)

    def _build_statusbar(self):
        sb = self.statusBar()
        self.status_label = QLabel("Bereit – Datei öffnen oder hierher ziehen")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        self.sm_label = QLabel("")
        self.version_label = QLabel(f"v{VERSION}")
        sb.addWidget(self.status_label, 1)
        sb.addPermanentWidget(self.sm_label)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.version_label)

    # ------------------------------------------------------------ Settings
    def _restore_settings(self):
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = self.settings.value("windowState")
        if state is not None:
            self.restoreState(state)
        mode = self.settings.value("displayMode", "schattiert_kanten")
        self.mode_actions.get(mode, self.mode_actions["schattiert_kanten"]).setChecked(True)
        self.viewport.display_mode = mode if mode in self.mode_actions else "schattiert_kanten"
        bg = self.settings.value("background", "dunkel")
        bg = bg if bg in self.bg_actions else "dunkel"
        self.bg_actions[bg].setChecked(True)
        self.viewport.set_background(bg)
        q = self.settings.value("quality", "normal")
        self.quality = q if q in self.quality_actions else "normal"
        self.quality_actions[self.quality].setChecked(True)
        persp = self.settings.value("perspective", False, type=bool)
        self.a_persp.setChecked(persp)
        self.viewport.camera.SetParallelProjection(not persp)
        axes = self.settings.value("axes", True, type=bool)
        self.a_axes.setChecked(axes)
        self._update_recent_menu()
        if self.settings.value("spacemouse", False, type=bool) and spacemouse.available():
            self.a_spacemouse.setChecked(True)
            QTimer.singleShot(500, lambda: self._toggle_spacemouse(True))

    def closeEvent(self, ev):  # noqa: N802
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.setValue("displayMode", self.viewport.display_mode)
        self.settings.setValue("background", self.viewport.background)
        self.settings.setValue("quality", self.quality)
        self.settings.setValue("perspective", self.a_persp.isChecked())
        self.settings.setValue("axes", self.a_axes.isChecked())
        self.settings.setValue("spacemouse", self.a_spacemouse.isChecked())
        if self.sm_thread is not None:
            self.sm_thread.stop()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        self.viewport.Finalize()
        super().closeEvent(ev)

    def showEvent(self, ev):  # noqa: N802
        super().showEvent(ev)
        if not getattr(self, "_vtk_started", False):
            self._vtk_started = True
            self.viewport.initialize()
            self.viewport.set_axes_visible(self.a_axes.isChecked())

    # --------------------------------------------------------------- Laden
    def open_dialog(self):
        start_dir = self.settings.value("lastDir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self, "CAD-Datei öffnen", start_dir, FILE_FILTER)
        if path:
            self.open_file(path)

    def reload(self):
        if self.model is not None:
            self.open_file(self.model.path)

    def open_file(self, path: str):
        if self._thread is not None and self._thread.isRunning():
            self.statusBar().showMessage("Es wird bereits eine Datei geladen …", 3000)
            return
        if not path.lower().endswith(SUPPORTED_EXT):
            QMessageBox.warning(self, APP_NAME, f"Dieses Dateiformat wird nicht unterstützt:\n{path}")
            return
        self.settings.setValue("lastDir", os.path.dirname(path))
        self._add_recent(path)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.status_label.setText(f"Lade {os.path.basename(path)} …")
        self.a_open.setEnabled(False)
        self.a_reload.setEnabled(False)
        QApplication.setOverrideCursor(Qt.BusyCursor)

        self._thread = QThread(self)
        self._worker = LoadWorker(path, self.quality)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_loaded)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    @Slot(int, str)
    def _on_progress(self, pct: int, text: str):
        self.progress.setValue(pct)
        self.status_label.setText(text)

    def _end_loading(self):
        QApplication.restoreOverrideCursor()
        self.progress.setVisible(False)
        self.a_open.setEnabled(True)
        self.a_reload.setEnabled(True)

    @Slot(object, float)
    def _on_loaded(self, model: LoadedModel, seconds: float):
        self._end_loading()
        self.model = model
        self._load_time = seconds
        self.part_map = self.viewport.set_model(model)
        self._fill_tree(model.root)
        self.setWindowTitle(f"{os.path.basename(model.path)} – {APP_NAME}  v{VERSION}")
        tris = f"{model.triangles:,}".replace(",", ".")
        secs = f"{seconds:.1f}".replace(".", ",")
        self.status_label.setText(
            f"{os.path.basename(model.path)} geladen – {model.parts} Bauteile, {tris} Dreiecke in {secs} s"
        )
        self._update_info()

    @Slot(str)
    def _on_failed(self, message: str):
        self._end_loading()
        self.status_label.setText("Laden fehlgeschlagen")
        QMessageBox.critical(self, APP_NAME, message)

    # ------------------------------------------------------- Zuletzt geöffnet
    def _recent(self) -> list:
        val = self.settings.value("recent", [])
        if isinstance(val, str):
            val = [val]
        return [p for p in (val or []) if isinstance(p, str)]

    def _add_recent(self, path: str):
        files = [p for p in self._recent() if os.path.normcase(p) != os.path.normcase(path)]
        files.insert(0, path)
        self.settings.setValue("recent", files[:MAX_RECENT])
        self._update_recent_menu()

    def _update_recent_menu(self):
        self.recent_menu.clear()
        files = [p for p in self._recent() if os.path.isfile(p)]
        if not files:
            a = self.recent_menu.addAction("(leer)")
            a.setEnabled(False)
            return
        for i, p in enumerate(files, start=1):
            a = self.recent_menu.addAction(f"{i}  {os.path.basename(p)}")
            a.setToolTip(p)
            a.triggered.connect(lambda _=False, path=p: self.open_file(path))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Liste leeren", lambda: (self.settings.setValue("recent", []),
                                                            self._update_recent_menu()))

    # ---------------------------------------------------------------- Baum
    def _fill_tree(self, root: ModelNode):
        self._updating_tree = True
        self.tree.clear()
        self.item_for_node.clear()

        def add(node: ModelNode, parent):
            item = QTreeWidgetItem(parent)
            label = node.name or ("Baugruppe" if node.is_assembly else "Bauteil")
            item.setText(0, label)
            item.setToolTip(0, label)
            item.setData(0, ROLE_NODE, node)
            flags = item.flags() | Qt.ItemIsUserCheckable
            if node.children:
                flags |= Qt.ItemIsAutoTristate
            item.setFlags(flags)
            item.setCheckState(0, Qt.Checked)
            self.item_for_node[id(node)] = item
            for c in node.children:
                add(c, item)
            return item

        top = add(root, self.tree)
        top.setExpanded(True)
        self.tree.setHeaderLabels([f"Modellbaum ({root.leaf_count()} Bauteile)"])
        self._updating_tree = False

    def _leaf_nodes(self, item: QTreeWidgetItem) -> list:
        node = item.data(0, ROLE_NODE)
        if node is None:
            return []
        out = []

        def visit(n):
            if n.mesh_key:
                out.append(n)
            for c in n.children:
                visit(c)

        visit(node)
        return out

    def _parts_of_items(self, items) -> list:
        parts = []
        seen = set()
        for it in items:
            for n in self._leaf_nodes(it):
                p = self.part_map.get(id(n))
                if p is not None and id(p) not in seen:
                    seen.add(id(p))
                    parts.append(p)
        return parts

    @Slot(QTreeWidgetItem, int)
    def _on_item_changed(self, item, column):
        if self._updating_tree:
            return
        # Nur Blätter steuern die Sichtbarkeit; Qt aktualisiert Eltern automatisch
        it = QTreeWidgetItemIterator(item)
        while it.value():
            cur = it.value()
            if cur is not item and not self._is_descendant(cur, item):
                break
            node = cur.data(0, ROLE_NODE)
            if node is not None and node.mesh_key:
                p = self.part_map.get(id(node))
                if p is not None:
                    self.viewport.set_part_visible(p, cur.checkState(0) == Qt.Checked)
            it += 1
        self.viewport.render()

    @staticmethod
    def _is_descendant(item, ancestor) -> bool:
        p = item.parent()
        while p is not None:
            if p is ancestor:
                return True
            p = p.parent()
        return False

    def _on_tree_selection(self):
        parts = self._parts_of_items(self.tree.selectedItems())
        self.viewport.set_selection(parts)
        self._update_info(parts)

    @Slot(object)
    def _on_part_picked(self, node):
        if node is None:
            self.tree.clearSelection()
            return
        item = self.item_for_node.get(id(node))
        if item is None:
            return
        mods = QApplication.keyboardModifiers()
        if not (mods & (Qt.ControlModifier | Qt.ShiftModifier)):
            self.tree.clearSelection()
        item.setSelected(not item.isSelected() if mods & Qt.ControlModifier else True)
        self.tree.scrollToItem(item)

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        if item is not None:
            if not item.isSelected():
                self.tree.clearSelection()
                item.setSelected(True)
            menu.addAction(self.a_fit_sel)
            menu.addAction(self.a_isolate)
            menu.addAction(self.a_hide_sel)
            menu.addSeparator()
        menu.addAction(self.a_show_all)
        menu.addAction("Alles aufklappen", self.tree.expandAll)
        menu.addAction("Alles zuklappen", self.tree.collapseAll)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _set_checked(self, item, state):
        item.setCheckState(0, state)

    def _show_all(self):
        if self.tree.topLevelItemCount():
            self._set_checked(self.tree.topLevelItem(0), Qt.Checked)

    def _hide_selection(self):
        for it in self.tree.selectedItems():
            self._set_checked(it, Qt.Unchecked)

    def _isolate_selection(self):
        sel = self.tree.selectedItems()
        if not sel or not self.tree.topLevelItemCount():
            return
        self._set_checked(self.tree.topLevelItem(0), Qt.Unchecked)
        for it in sel:
            self._set_checked(it, Qt.Checked)
        self._fit_selection()

    def _fit_selection(self):
        parts = self._parts_of_items(self.tree.selectedItems())
        self.viewport.fit(parts if parts else None)

    # --------------------------------------------------------------- Info
    def _update_info(self, selected=None):
        if self.model is None:
            self.info.setText(
                "<p style='color:#8a93a3'>Kein Modell geladen.<br><br>"
                "Unterstützt: STEP (.stp/.step), IGES, BREP, STL<br>"
                "Datei per Drag &amp; Drop ins Fenster ziehen.</p>"
            )
            return
        m = self.model
        b = m.bounds
        size = os.path.getsize(m.path) if os.path.isfile(m.path) else 0

        def fmt(v):
            return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        rows = [
            ("Datei", os.path.basename(m.path)),
            ("Größe", f"{size / 1024 / 1024:.2f} MB".replace(".", ",")),
            ("Bauteile", f"{m.parts} ({m.unique_parts} verschiedene)"),
            ("Dreiecke", f"{m.triangles:,}".replace(",", ".")),
            ("Abmessung X", f"{fmt(b[1] - b[0])} mm"),
            ("Abmessung Y", f"{fmt(b[3] - b[2])} mm"),
            ("Abmessung Z", f"{fmt(b[5] - b[4])} mm"),
            ("Ladezeit", f"{self._load_time:.1f} s".replace(".", ",")),
        ]
        html = "<table cellspacing='2'>"
        for k, v in rows:
            html += f"<tr><td style='color:#8a93a3;padding-right:8px'>{k}</td><td>{v}</td></tr>"
        html += "</table>"
        if selected:
            sb = self.viewport.visible_bounds(selected)
            names = ", ".join(sorted({p.node.name for p in selected})[:3])
            if len({p.node.name for p in selected}) > 3:
                names += " …"
            html += f"<hr><b style='color:#ff8c1a'>Auswahl</b> ({len(selected)})<br>{names}"
            if sb:
                html += (
                    f"<br><span style='color:#8a93a3'>X</span> {fmt(sb[1] - sb[0])} &nbsp;"
                    f"<span style='color:#8a93a3'>Y</span> {fmt(sb[3] - sb[2])} &nbsp;"
                    f"<span style='color:#8a93a3'>Z</span> {fmt(sb[5] - sb[4])} mm"
                )
        self.info.setText(html)

    # ------------------------------------------------------------ Ansicht
    def _set_mode(self, mode):
        self.viewport.set_display_mode(mode)

    def _toggle_transparent(self, on):
        self.viewport.set_transparent(on)

    def _toggle_perspective(self, on):
        self.viewport.set_parallel(not on)

    def _toggle_axes(self, on):
        self.viewport.set_axes_visible(on)

    def _set_background(self, name):
        self.viewport.set_background(name)

    def _set_quality(self, q):
        self.quality = q
        if self.model is not None:
            self.statusBar().showMessage("Neue Qualität gilt ab dem nächsten Laden (F5)", 4000)

    def _toggle_measure(self, on):
        self.viewport.set_measure_mode(on)

    @Slot(float, tuple)
    def _on_measured(self, dist, d):
        def f(v):
            return f"{v:.3f}".replace(".", ",")

        self.statusBar().showMessage(
            f"Abstand {f(dist)} mm   (ΔX {f(abs(d[0]))}  ΔY {f(abs(d[1]))}  ΔZ {f(abs(d[2]))})", 0)

    @Slot(str)
    def _on_measure_status(self, text):
        if text == "__ende__":
            self.a_measure.setChecked(False)
            self.status_label.setText("Messen beendet")
            return
        if text:
            self.status_label.setText(text)

    def save_screenshot(self):
        base = os.path.splitext(os.path.basename(self.model.path))[0] if self.model else "ansicht"
        start = os.path.join(self.settings.value("lastShotDir", os.path.expanduser("~")), f"{base}.png")
        path, _ = QFileDialog.getSaveFileName(self, "Bild speichern", start, "PNG-Bild (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        self.settings.setValue("lastShotDir", os.path.dirname(path))
        self.viewport.save_screenshot(path)
        self.statusBar().showMessage(f"Bild gespeichert: {path}", 5000)

    # ---------------------------------------------------------- SpaceMouse
    def _toggle_spacemouse(self, on):
        if on and self.sm_thread is None:
            self.sm_thread = spacemouse.SpaceMouseThread(self)
            self.sm_thread.motion.connect(self.viewport.apply_spacemouse)
            self.sm_thread.button.connect(self._on_sm_button)
            self.sm_thread.status.connect(self.sm_label.setText)
            self.sm_thread.finished.connect(self._on_sm_finished)
            self.sm_thread.start()
        elif not on and self.sm_thread is not None:
            self.sm_thread.stop()
            self.sm_thread = None
            self.sm_label.setText("")

    def _on_sm_finished(self):
        self.sm_thread = None

    def _on_sm_button(self, idx):
        if idx == 0:
            self.viewport.fit()
        elif idx == 1:
            self.viewport.set_view("iso")

    # --------------------------------------------------------------- Hilfe
    def _show_help(self):
        QMessageBox.information(
            self,
            "Bedienung",
            "<b>Maus</b><br>"
            "Linke Taste ziehen: Drehen<br>"
            "Rechte oder mittlere Taste ziehen: Verschieben<br>"
            "Mausrad: Zoomen<br>"
            "Klick auf Bauteil: Auswählen (Strg = Mehrfachauswahl)<br><br>"
            "<b>Tasten</b><br>"
            "F Einpassen · Shift+F Auswahl einpassen<br>"
            "0 Iso · 1 Vorne · 3 Rechts · 7 Oben (mit Strg: Gegenseite)<br>"
            "Strg+E / Strg+R / Strg+W Darstellung · T Transparent · P Perspektive<br>"
            "H Ausblenden · I Nur Auswahl · Strg+Shift+A Alle anzeigen<br>"
            "M Messen · Esc Messen beenden · F5 Neu laden<br><br>"
            "<b>SpaceMouse</b><br>"
            "Unter Werkzeuge aktivieren. Linke Taste: Einpassen, rechte Taste: Iso.<br>"
            "Falls das Gerät vom 3Dconnexion-Treiber belegt ist, kann der Viewer es nicht lesen.",
        )

    def _show_about(self):
        QMessageBox.about(
            self,
            f"Über {APP_NAME}",
            f"<h3>{APP_NAME}</h3>"
            f"<p>Version {VERSION}</p>"
            "<p>Eigenständiger 3D-Viewer für STEP-, IGES-, BREP- und STL-Dateien.</p>"
            "<p>Geometrie: Open CASCADE (OCP) · Darstellung: VTK · Oberfläche: Qt (PySide6)</p>",
        )

    # --------------------------------------------------------- Drag & Drop
    def dragEnterEvent(self, ev):  # noqa: N802
        if ev.mimeData().hasUrls():
            for url in ev.mimeData().urls():
                if url.toLocalFile().lower().endswith(SUPPORTED_EXT):
                    ev.acceptProposedAction()
                    return
        ev.ignore()

    def dropEvent(self, ev):  # noqa: N802
        for url in ev.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(SUPPORTED_EXT):
                self.open_file(path)
                break
