"""Check that a PyInstaller build lists critical Qt/ICU runtime files."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REQUIRED = (
    "PySide6\\Qt6Core.dll",
    "PySide6\\Qt6Gui.dll",
    "PySide6\\icuuc.dll",
    "PySide6\\plugins\\platforms\\qwindows.dll",
    "shiboken6\\Shiboken.pyd",
)

OPTIONAL_BUT_RECOMMENDED = (
    "PySide6\\icuin.dll",
    "PySide6\\icu.dll",
    "PySide6\\plugins\\styles\\qmodernwindowsstyle.dll",
    "PySide6\\plugins\\imageformats\\qjpeg.dll",
)


def _normalize(path: str) -> str:
    return path.replace("\\\\", "\\").replace("/", "\\")


def load_toc_paths(toc_path: Path) -> set[str]:
    text = toc_path.read_text(encoding="utf-8", errors="replace")
    bundled: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("('"):
            continue
        # Archive dest paths never include a Windows drive letter.
        match = re.match(r"\('([^']+)'\s*,", line)
        if not match:
            continue
        dest = _normalize(match.group(1))
        if ":" in dest[:3]:
            continue
        bundled.add(dest)
    return bundled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "toc",
        nargs="?",
        default="build/IchaLaunch/EXE-00.toc",
        help="Path to EXE-00.toc from the PyInstaller build",
    )
    args = parser.parse_args(argv)

    toc_path = Path(args.toc)
    if not toc_path.is_file():
        print(f"ERROR: toc not found: {toc_path}", file=sys.stderr)
        return 2

    bundled = load_toc_paths(toc_path)
    missing = [item for item in REQUIRED if item not in bundled]
    weak = [item for item in OPTIONAL_BUT_RECOMMENDED if item not in bundled]

    print(f"Checked {toc_path} ({len(bundled)} paths)")
    for item in REQUIRED:
        mark = "OK" if item in bundled else "MISSING"
        print(f"  [{mark}] {item}")
    for item in OPTIONAL_BUT_RECOMMENDED:
        if item not in bundled:
            print(f"  [warn] optional missing: {item}")

    if missing:
        print(f"\nFAIL: missing {len(missing)} required bundle entries", file=sys.stderr)
        return 1

    if weak:
        print(f"\nPASS with {len(weak)} optional warnings")
    else:
        print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
