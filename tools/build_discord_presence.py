#!/usr/bin/env python3
"""Build ichalaunch_discord.dll (32-bit) for VanillaFixes dlls.txt.

The binary is gitignored and packed into IchaLaunch.exe at spec time.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "_discord_presence" / "ichalaunch_discord.c"
OUT_DIR = ROOT / "tools" / "_discord_presence" / "out"
OUT_DLL = OUT_DIR / "ichalaunch_discord.dll"
PACKED_DIR = ROOT / "ichalaunch" / "data" / "discord_wow"
PACKED_DLL = PACKED_DIR / "ichalaunch_discord.dll"


def _find_zig() -> Path | None:
    bundled = ROOT / "tools" / "_weirdutils" / "zig"
    if bundled.is_dir():
        matches = sorted(bundled.rglob("zig.exe")) + sorted(bundled.rglob("zig"))
        for path in matches:
            if path.is_file():
                return path
    found = shutil.which("zig")
    return Path(found) if found else None


def build() -> Path:
    if not SRC.is_file():
        raise FileNotFoundError(f"Missing DLL source: {SRC}")
    zig = _find_zig()
    if zig is None:
        raise FileNotFoundError(
            "Zig not found. Install Zig or keep tools/_weirdutils/zig from the "
            "WeirdUtils build, then re-run: python tools/build_discord_presence.py"
        )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(zig),
        "cc",
        "-shared",
        "-target",
        "x86-windows-gnu",
        "-O2",
        "-s",
        "-o",
        str(OUT_DLL),
        str(SRC),
        "-lversion",
        "-lshell32",
        "-lkernel32",
        "-luser32",
    ]
    print(" ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))
    if not OUT_DLL.is_file():
        raise FileNotFoundError(f"Build produced no DLL at {OUT_DLL}")
    print(f"Wrote {OUT_DLL} ({OUT_DLL.stat().st_size} bytes)")
    PACKED_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUT_DLL, PACKED_DLL)
    print(f"Copied {PACKED_DLL} ({PACKED_DLL.stat().st_size} bytes)")
    return OUT_DLL


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Accepted for parity with tools/build_weirdutils.py",
    )
    parser.parse_args()
    build()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
