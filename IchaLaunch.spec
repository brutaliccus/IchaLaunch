# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import glob
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files
from PyInstaller.utils.hooks.qt import pyside6_library_info

block_cipher = None

def _theme_datas() -> list[tuple[str, str]]:
    """Pack theme chrome, but not the official_artworks gallery (fetched at runtime)."""
    theme = os.path.join("ichalaunch", "ui", "theme")
    out: list[tuple[str, str]] = []
    for root, dirs, files in os.walk(theme):
        dirs[:] = [name for name in dirs if name != "official_artworks"]
        if os.path.basename(root) == "official_artworks":
            continue
        dest = root.replace("\\", "/")
        for name in files:
            out.append((os.path.join(root, name), dest))
    return out


datas = [
    ("ichalaunch/data", "ichalaunch/data"),
    *_theme_datas(),
]

# Source-built WeirdUtils variants (gitignored). Fail the build if missing.
# Outline / DPSLog stay offered; Interact / Crash Fix stay packed but hidden.
_WEIRDUTILS_VARIANT_DLLS = (
    "outline.dll",
    "interact.dll",
    "framecrash.dll",
    "dpslog.dll",
)
_weirdutils_out = os.path.join("tools", "_weirdutils", "out")
for _name in _WEIRDUTILS_VARIANT_DLLS:
    _src = os.path.join(_weirdutils_out, _name)
    if not os.path.isfile(_src):
        raise SystemExit(
            f"Missing {_src}. Run: python tools/build_weirdutils.py --build-only"
        )
    datas.append((_src, "ichalaunch/data/weirdutils"))
binaries: list[tuple[str, str]] = []
hiddenimports = [
    # Ed25519 verification for signed launcher updates.
    "cryptography.hazmat.bindings._rust",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "shiboken6",
    "PySide6.support.deprecated",
]

# certifi CA bundle — every HTTPS stack in the onefile (catalog, addons, updates).
datas += collect_data_files("certifi")
hiddenimports.append("certifi")

# requests chooses its character-detection library at runtime, taking the first
# of chardet / charset_normalizer that imports. That choice goes through
# importlib, so it is invisible to PyInstaller's analysis and would otherwise be
# decided by whatever the build machine happens to have installed.
#
# charset_normalizer is a hard dependency of requests and is always present.
# chardet is only an optional extra (requests[use-chardet-on-py3]), is not in
# requirements.txt, and arrives incidentally through unrelated packages. When it
# does it wins, and it costs about 50 ms of every launch plus 12 MB of language
# frequency models for Welsh, Thai, Slovak and friends, to do a job
# charset_normalizer already does. Pinning the choice here makes the build
# reproducible rather than dependent on the machine it was made on.
cn_datas, cn_binaries, cn_hidden = collect_all("charset_normalizer")
datas += cn_datas
binaries += cn_binaries
hiddenimports += cn_hidden

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
    excludes=["chardet"],
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
    icon="ichalaunch/ui/theme/ravencraft_icon.ico",
)
