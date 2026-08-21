"""Custom WoW theme cursors (Point default, OpenHand for clickable UI)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QCursor, QPixmap
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from ichalaunch.core.paths import theme_file

_POINT_NAME = "cursor_point.png"
_POINT_EXTERNAL = Path(r"F:\wow-ui-textures\CURSOR\Point.PNG")
# Tip is top-left of the 32×32 Point PNG.
_POINT_HOTSPOT = (0, 0)

_OPENHAND_NAME = "cursor_openhand.png"
_OPENHAND_EXTERNAL = Path(r"F:\wow-ui-textures\CURSOR\OPENHAND.PNG")
# 32×32 OPENHAND — opaque bbox ~ (5,4)–(24,27); upper-center of palm.
_OPENHAND_HOTSPOT = (14, 11)

_CACHE: dict[str, QCursor | None] = {}


def _load_cursor(bundled: str, external: Path, hotspot: tuple[int, int]) -> QCursor | None:
    key = f"{bundled}:{hotspot[0]},{hotspot[1]}"
    if key in _CACHE:
        return _CACHE[key]
    path = theme_file(bundled)
    if not path.is_file():
        path = external
    if not path.is_file():
        _CACHE[key] = None
        return None
    pm = QPixmap(str(path))
    if pm.isNull():
        _CACHE[key] = None
        return None
    cur = QCursor(pm, hotspot[0], hotspot[1])
    _CACHE[key] = cur
    return cur


def point_cursor() -> QCursor | None:
    """Default window arrow (Point.PNG)."""
    return _load_cursor(_POINT_NAME, _POINT_EXTERNAL, _POINT_HOTSPOT)


def open_hand_cursor() -> QCursor | None:
    """Clickable / link cursor (OPENHAND.PNG)."""
    return _load_cursor(_OPENHAND_NAME, _OPENHAND_EXTERNAL, _OPENHAND_HOTSPOT)


def apply_open_hand(widget: QWidget) -> None:
    """Prefer OpenHand; fall back to Qt pointing hand if the asset is missing."""
    cur = open_hand_cursor()
    if cur is not None:
        widget.setCursor(cur)
    else:
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
