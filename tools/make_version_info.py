"""Erzeugt build/version_info.txt (Windows-Dateiversion der exe) und gibt die Version aus.

Aufruf:  python tools/make_version_info.py
"""

from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_version() -> str:
    with open(os.path.join(ROOT, "stepviewer", "__init__.py"), encoding="utf-8") as fh:
        return re.search(r'VERSION\s*=\s*"([^"]+)"', fh.read()).group(1)


def main():
    version = read_version()
    nums = [int(x) for x in re.findall(r"\d+", version)][:4]
    nums += [0] * (4 - len(nums))
    tup = ", ".join(str(n) for n in nums)
    text = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040704B0', [
      StringStruct('CompanyName', 'Scorch1262'),
      StringStruct('FileDescription', 'STEP-Viewer-MK1 – 3D-Viewer für STEP/IGES/STL'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'STEP-Viewer-MK1'),
      StringStruct('OriginalFilename', 'STEP-Viewer-MK1.exe'),
      StringStruct('ProductName', 'STEP-Viewer-MK1'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1031, 1200])])
  ]
)
"""
    os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
    with open(os.path.join(ROOT, "build", "version_info.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(version)


if __name__ == "__main__":
    main()
