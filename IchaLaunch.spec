# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import glob
import os
import sys

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks.qt import pyside6_library_info

block_cipher = None

datas = [
    ("ichalaunch/data", "ichalaunch/data"),
    ("ichalaunch/ui/theme", "ichalaunch/ui/theme"),
]
binaries: list[tuple[str, str]] = []
hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "shiboken6",
    "PySide6.support.deprecated",
]

# PySide6 + shiboken6 (plugins, translations, Qt DLLs, .pyd modules).
for package in ("PySide6", "shiboken6"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# PyInstaller's Qt hook only finds versioned ICU DLLs (icuuc73.dll, …) inside the
# wheel. PySide6 on Windows links against unversioned icuuc.dll from the OS, which
# is not shipped in site-packages — bundle it next to Qt6Core.dll for onefile.
if pyside6_library_info.version is not None:
    binaries += pyside6_library_info.collect_extra_binaries()

pyside6_root = pyside6_library_info.package_location
qt_search_dirs = [
    pyside6_root,
    os.path.join(pyside6_root, "Qt6", "bin"),
]

seen_binaries: set[tuple[str, str]] = set()

def _add_binary(src: str, dest: str) -> None:
    key = (src, dest)
    if not os.path.isfile(src):
        return
    if key in seen_binaries:
        return
    binaries.append((src, dest))
    seen_binaries.add(key)

# Explicit Qt6 runtime DLLs (wheel layout varies by PySide6 version).
for search_dir in qt_search_dirs:
    if not os.path.isdir(search_dir):
        continue
    for pattern in ("Qt6*.dll", "icu*.dll"):
        for dll_path in sorted(glob.glob(os.path.join(search_dir, pattern))):
            _add_binary(dll_path, "PySide6")

# Windows ICU: Qt6Core.dll imports icuuc.dll; Proton/Wine and some Win10 builds
# do not provide it on the loader search path once extracted to _MEIPASS.
if sys.platform == "win32":
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    for name in ("icuuc.dll", "icuin.dll", "icu.dll"):
        _add_binary(os.path.join(system32, name), "PySide6")

    # Ensure platform + style + image format plugins are present.
    plugin_groups = (
        "platforms/qwindows.dll",
        "styles/qmodernwindowsstyle.dll",
        "imageformats/qgif.dll",
        "imageformats/qico.dll",
        "imageformats/qjpeg.dll",
        "imageformats/qsvg.dll",
    )
    for rel in plugin_groups:
        src = os.path.join(pyside6_root, "plugins", rel.replace("/", os.sep))
        dest_dir = os.path.join("PySide6", "plugins", os.path.dirname(rel)).replace("\\", "/")
        if os.path.isfile(src):
            _add_binary(src, dest_dir)

# UPX breaks many Qt/ICU/MSVC DLLs in onefile bundles.
upx_exclude = []
for _src, dest in binaries:
    base = os.path.basename(_src).lower()
    if (
        base.startswith("qt6")
        or base.startswith("icu")
        or base.startswith("pyside6")
        or base.startswith("shiboken")
        or base in {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"}
        or "plugins" in dest.replace("\\", "/")
    ):
        upx_exclude.append(os.path.basename(_src))

a = Analysis(
    ["run.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="IchaLaunch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=sorted(set(upx_exclude)),
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="ichalaunch/ui/theme/ichalaunch.ico",
)
