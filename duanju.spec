# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：onedir，双击 exe 运行。"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH)
assets = ROOT / "assets"
datas = ([(str(assets), "assets")] if assets.is_dir() else []) + [('mpv-2.dll', '.')]
icon = assets / "app.ico"

# Version resource: single source is duanju/version.py; written into the exe properties
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from duanju.version import VERSION as _version  # noqa: E402
from make_version_info import write_version_file  # noqa: E402

version_file = write_version_file(ROOT / "build" / "version_info.txt", _version)
print(f"[duanju.spec] build version: {_version}")

a = Analysis(
    ["app.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "PySide6.QtNetwork",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtBluetooth",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.Qt3DCore",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DuanjuDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(icon) if icon.is_file() else None,
    version=str(version_file),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DuanjuDownloader",
)
