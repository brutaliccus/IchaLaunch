"""Resource path helpers for source vs frozen (PyInstaller)."""

from __future__ import annotations

import sys
from pathlib import Path


def package_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "ichalaunch"
    # ichalaunch/core/paths.py → ichalaunch/
    return Path(__file__).resolve().parent.parent


def repo_root() -> Path:
    """Checkout root when running from source; executable folder when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return package_root().parent


def data_file(*parts: str) -> Path:
    return package_root().joinpath("data", *parts)


def theme_file(*parts: str) -> Path:
    return package_root().joinpath("ui", "theme", *parts)
