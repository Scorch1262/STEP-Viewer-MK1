# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Spezifikation für STEP-Viewer-MK1 (Windows-exe und macOS-App)
#
# Bauen:  pyinstaller --noconfirm STEP-Viewer-MK1.spec

import os
import re
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = os.path.abspath(SPECPATH)  # noqa: F821

with open(os.path.join(ROOT, "stepviewer", "__init__.py"), encoding="utf-8") as fh:
    VERSION = re.search(r'VERSION\s*=\s*"([^"]+)"', fh.read()).group(1)

APP_NAME = "STEP-Viewer-MK1"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

datas = [(os.path.join(ROOT, "assets"), "assets")]
datas += collect_data_files("OCP")
binaries = collect_dynamic_libs("OCP")

hiddenimports = [
    "OCP.OCP",
    "vtkmodules.vtkRenderingOpenGL2",
    "vtkmodules.vtkRenderingContextOpenGL2",
    "vtkmodules.vtkRenderingFreeType",
    "vtkmodules.vtkInteractionStyle",
    "vtkmodules.vtkInteractionWidgets",
    "vtkmodules.vtkRenderingAnnotation",
    "vtkmodules.vtkIOImage",
    "vtkmodules.vtkIOGeometry",
    "vtkmodules.util.data_model",
    "vtkmodules.util.execution_model",
    "hid",
]

excludes = [
    "tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pytest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtNetwork", "PySide6.QtSql",
    "PyQt5", "PyQt6",
]

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

icon_file = os.path.join(ROOT, "assets", "icon.icns" if IS_MAC else "icon.ico")
version_file = os.path.join(ROOT, "build", "version_info.txt") if IS_WIN else None
if version_file and not os.path.isfile(version_file):
    version_file = None

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon_file,
    version=version_file,
    target_arch=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

if IS_MAC:
    doc_types = [
        {
            "CFBundleTypeName": "STEP-Datei",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "CFBundleTypeExtensions": ["step", "stp", "STEP", "STP"],
        },
        {
            "CFBundleTypeName": "IGES-Datei",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "CFBundleTypeExtensions": ["iges", "igs", "IGES", "IGS"],
        },
        {
            "CFBundleTypeName": "STL-Datei",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "CFBundleTypeExtensions": ["stl", "STL"],
        },
        {
            "CFBundleTypeName": "BREP-Datei",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "CFBundleTypeExtensions": ["brep", "brp"],
        },
    ]
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{APP_NAME}.app",
        icon=icon_file,
        bundle_identifier="de.scorch1262.stepviewermk1",
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "CFBundleDocumentTypes": doc_types,
            "NSHumanReadableCopyright": "Scorch1262",
        },
    )
