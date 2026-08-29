"""Populate tools/_weirdutils/ with WeirdUtils release DLLs and source-built variants.

Outputs are gitignored. Requires git, network access, and Zig 0.16 to compile
the four modules that never shipped on the Codeberg releases page.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORK = REPO_ROOT / "tools" / "_weirdutils"
SRC = WORK / "src"
PREBUILT = WORK / "prebuilt"
OUT = WORK / "out"

CLONE_URL = "https://codeberg.org/MarcelineVQ/WeirdUtils.git"
RELEASE_BASE = "https://codeberg.org/MarcelineVQ/WeirdUtils/releases/download"
ZIG_VERSION = "0.16.0"
ZIG_ZIP_NAME = f"zig-x86_64-windows-{ZIG_VERSION}.zip"
ZIG_URL = f"https://ziglang.org/download/{ZIG_VERSION}/{ZIG_ZIP_NAME}"
ZIG_DIR = WORK / "zig"

# Official assets: v0.7.0 has most individuals; v0.7.1 refreshes the combo + markers.
PREBUILT_ASSETS: tuple[tuple[str, str], ...] = (
    ("v0.7.0", "clickthrough.dll"),
    ("v0.7.0", "customassets.dll"),
    ("v0.7.0", "healtextfix.dll"),
    ("v0.7.0", "logsessions.dll"),
    ("v0.7.0", "minimapicons.dll"),
    ("v0.7.0", "pngscreenshots.dll"),
    ("v0.7.0", "transmogfix.dll"),
    ("v0.7.0", "weirdperformance.dll"),
    ("v0.7.1", "worldmarkers.dll"),
)

# User-facing modules that exist in source but were not on the last releases.
BUILD_MODULES: tuple[str, ...] = ("outline", "interact", "framecrash", "dpslog")
# Default-on modules we do not compile (release DLLs already cover them).
# weirdperformance also fails on Zig 0.16.0 asm clobber syntax.
SKIP_DEFAULT_MODULES: tuple[str, ...] = (
    "pngscreenshots",
    "worldmarkers",
    "logsessions",
    "minimapicons",
    "transmogfix",
    "customassets",
    "healtextfix",
    "bigcursor",
    "clickthrough",
    "weirdperformance",
)

_DLLMAIN_OLD = """pub export fn DllMain(
    _: ?*anyopaque,
    reason: u32,
    _: ?*anyopaque,
) callconv(WINAPI) i32 {
    switch (reason) {
        1 => install(),
        0 => uninstall(),
        else => {},
    }
    return 1;
}"""
_DLLMAIN_NEW = """pub export fn DllMain(
    _: ?*anyopaque,
    reason: u32,
    _: ?*anyopaque,
) callconv(WINAPI) std.os.windows.BOOL {
    switch (reason) {
        1 => install(),
        0 => uninstall(),
        else => {},
    }
    return .TRUE;
}"""

_UA = "IchaLaunch-WeirdUtilsBuild/1.0"


def _run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        capture_output=True,
    )


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    if dest.stat().st_size < 1024:
        raise RuntimeError(f"Download too small ({dest.stat().st_size} bytes): {url}")


def download_prebuilt() -> None:
    PREBUILT.mkdir(parents=True, exist_ok=True)
    for tag, name in PREBUILT_ASSETS:
        dest = PREBUILT / name
        url = f"{RELEASE_BASE}/{tag}/{name}"
        print(f"Downloading {tag}/{name}...")
        _download(url, dest)


def _apply_zig016_compat_patches() -> None:
    """Zig 0.16.0 made windows.BOOL an enum; published source still returns i32."""
    main_zig = SRC / "src" / "main.zig"
    text = main_zig.read_text(encoding="utf-8")
    patched = text
    if _DLLMAIN_OLD in patched:
        patched = patched.replace(_DLLMAIN_OLD, _DLLMAIN_NEW, 1)
    if patched == text:
        return
    main_zig.write_text(patched, encoding="utf-8")
    print(f"Patched {main_zig.relative_to(SRC)} for Zig 0.16.0 BOOL")


def clone_or_update_source() -> None:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is required to clone WeirdUtils source")
    if (SRC / ".git").is_dir():
        print(f"Updating {SRC}...")
        _run([git, "-C", str(SRC), "fetch", "--depth", "1", "origin"], cwd=REPO_ROOT)
        _run([git, "-C", str(SRC), "reset", "--hard", "FETCH_HEAD"], cwd=REPO_ROOT)
        _apply_zig016_compat_patches()
        return
    if SRC.exists():
        shutil.rmtree(SRC)
    print(f"Cloning {CLONE_URL}...")
    WORK.mkdir(parents=True, exist_ok=True)
    _run([git, "clone", "--depth", "1", CLONE_URL, str(SRC)], cwd=REPO_ROOT)
    _apply_zig016_compat_patches()


def _zig_version(zig: str) -> str:
    proc = subprocess.run([zig, "version"], check=True, text=True, capture_output=True)
    return (proc.stdout or proc.stderr or "").strip()


def _portable_zig_exe() -> Path | None:
    if not ZIG_DIR.is_dir():
        return None
    hits = sorted(ZIG_DIR.rglob("zig.exe"))
    return hits[0] if hits else None


def _bootstrap_portable_zig() -> str:
    existing = _portable_zig_exe()
    if existing and existing.is_file():
        return str(existing)
    ZIG_DIR.mkdir(parents=True, exist_ok=True)
    archive = ZIG_DIR / ZIG_ZIP_NAME
    print(f"Downloading portable Zig {ZIG_VERSION}...")
    _download(ZIG_URL, archive)
    print(f"Extracting {archive.name}...")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(ZIG_DIR)
    exe = _portable_zig_exe()
    if not exe:
        raise RuntimeError(f"Zig zip extracted but zig.exe was not found in {ZIG_DIR}")
    return str(exe)


def _require_zig() -> str:
    zig = shutil.which("zig")
    if zig:
        version = _zig_version(zig)
        if version.startswith("0.16"):
            print(f"Using Zig {version}")
            return zig
        print(f"System Zig is {version}; bootstrapping {ZIG_VERSION} instead")
    zig = _bootstrap_portable_zig()
    version = _zig_version(zig)
    if not version.startswith("0.16"):
        raise RuntimeError(f"Zig 0.16 is required, found {version or 'unknown'}")
    print(f"Using Zig {version} ({zig})")
    return zig


def _find_built_dll(name: str) -> Path:
    zig_out = SRC / "zig-out"
    candidates = [
        zig_out / "variants" / f"{name}.dll",
        zig_out / "bin" / f"{name}.dll",
    ]
    for path in candidates:
        if path.is_file():
            return path
    if zig_out.is_dir():
        hits = sorted(zig_out.rglob(f"{name}.dll"))
        if hits:
            return hits[0]
    raise FileNotFoundError(
        f"zig build did not produce {name}.dll under {zig_out}"
    )


def build_missing_modules() -> None:
    zig = _require_zig()
    _apply_zig016_compat_patches()
    flags = [f"-D{name}=false" for name in SKIP_DEFAULT_MODULES]
    flags.extend(f"-D{name}=true" for name in BUILD_MODULES)
    cmd = [zig, "build", "all-variants", "-Doptimize=ReleaseSmall", *flags]
    print("Building:", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(SRC), text=True, capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
        raise RuntimeError(f"zig build failed with exit {proc.returncode}")
    OUT.mkdir(parents=True, exist_ok=True)
    for name in BUILD_MODULES:
        built = _find_built_dll(name)
        dest = OUT / f"{name}.dll"
        shutil.copy2(built, dest)
        print(f"Wrote {dest} ({dest.stat().st_size} bytes)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Fetch Codeberg release DLLs only (skip clone/build)",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Clone/update source and compile missing modules only",
    )
    args = parser.parse_args(argv)
    WORK.mkdir(parents=True, exist_ok=True)
    try:
        if not args.build_only:
            download_prebuilt()
        if not args.download_only:
            clone_or_update_source()
            build_missing_modules()
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Done. Prebuilt: {PREBUILT}  Built: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
