"""Reusable UI widgets."""
from __future__ import annotations
import base64
import hashlib
import json
import logging
import math
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from shiboken6 import isValid as _shiboken_is_valid
except ImportError:

    def _shiboken_is_valid(obj: object) -> bool:  # type: ignore[misc]
        return obj is not None
from urllib.parse import urlparse
from urllib.request import urlopen
from PySide6.QtCore import QObject, QPoint, QProcess, QRect, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ichalaunch.addons.catalog import is_turtle_wow_custom_addon
from ichalaunch.core.paths import theme_file
from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.widgets.glue_panel_button import (
    GLUE_BTN_H,
    GLUE_ROW_H,
    GLUE_ROW_MENU_W,
    GLUE_ROW_W,
    GluePanelButton,
    check_button_glow_for_plate,
    glue_row_square_chrome,
    open_git_icon_pixmap,
)
from ichalaunch.ui.widgets.theme_checkbox import ThemeCheckBox
from ichalaunch.ui.widgets.update_alert_badge import UpdateAlertBadge

log = logging.getLogger("ichalaunch")

_OPTIONS_COG = "UI-OptionsButton.PNG"
_OPTIONS_COG_EXTERNAL = Path(r"F:\wow-ui-textures\Buttons") / _OPTIONS_COG
_OPTIONS_COG_PX = 20
_OPTIONS_COG_CACHE: QPixmap | None = None

_PASS_UP = "UI-GroupLoot-Pass-Up.PNG"
_PASS_DOWN = "UI-GroupLoot-Pass-Down.PNG"
_PASS_EXTERNAL = Path(r"F:\wow-ui-textures\Buttons")
_PASS_ICON_PX = _OPTIONS_COG_PX  # match settings cog / reinstall icon size

_REFRESH_BTN = "UI-RefreshButton.PNG"
_REFRESH_EXTERNAL = Path(r"F:\wow-ui-textures\Buttons") / _REFRESH_BTN
_REFRESH_CACHE: QPixmap | None = None
_FOLDER_BTN = "folder.png"
_FOLDER_CACHE: QPixmap | None = None
_PASS_CACHE: dict[str, QPixmap] = {}
# Nudge reinstall icon down so its visual bottom matches the delete/pass icon.
# Refresh art is 1px shorter at the bottom than Pass; +1 aligns bottoms (was +3, overshot).
_REINSTALL_ICON_Y_NUDGE = 1
# Tight gap between adjacent addon-row icon actions (reinstall/delete/cog).
_ADDON_ROW_ACTION_GAP = 0
# Inline Open-in-Git beside the addon title (smaller than row action plates).
_OPEN_GIT_INLINE_PX = 20
_OPEN_GIT_INLINE_HIT = 22

# Addons catalog Prev/Next — WoW spellbook page-turn icons (Up idle, Down pressed).
_SPELLBOOK_NEXT_UP = "UI-SpellbookIcon-NextPage-Up.PNG"
_SPELLBOOK_NEXT_DOWN = "UI-SpellbookIcon-NextPage-Down.PNG"
_SPELLBOOK_PREV_UP = "UI-SpellbookIcon-PrevPage-Up.PNG"
_SPELLBOOK_PREV_DOWN = "UI-SpellbookIcon-PrevPage-Down.PNG"
# Home gallery Prev/Next — Disabled-state glyphs (grey) on the art wash.
_SPELLBOOK_NEXT_DISABLED = "UI-SpellbookIcon-NextPage-Disabled.PNG"
_SPELLBOOK_PREV_DISABLED = "UI-SpellbookIcon-PrevPage-Disabled.PNG"
_SPELLBOOK_EXTERNAL = Path(r"F:\wow-ui-textures\Buttons")
_SPELLBOOK_CACHE: dict[tuple[str, bool, int, str], QPixmap] = {}
# Match GluePanelButton toolbar height; square hit target for the 32² art.
_SPELLBOOK_PAGE_H = GLUE_BTN_H
_SPELLBOOK_ICON_PX = GLUE_BTN_H
# RGB sum at or below this is a BLP black well (punched out on the Home wash).
_SPELLBOOK_WELL_SUM = 64
_SPELLBOOK_IDLE_OPACITY = 0.80
_SPELLBOOK_HOVER_OPACITY = 1.0
_SPELLBOOK_PRESS_OPACITY = 0.85
_SPELLBOOK_DISABLED_OPACITY = 0.40


def _options_cog_pixmap() -> QPixmap:
    """Bundled WoW UI-OptionsButton, scaled for the addons row cog."""
    global _OPTIONS_COG_CACHE
    if _OPTIONS_COG_CACHE is not None:
        return _OPTIONS_COG_CACHE
    path = theme_file(_OPTIONS_COG)
    if not path.is_file():
        path = _OPTIONS_COG_EXTERNAL
    pm = QPixmap()
    if path.is_file():
        src = QPixmap(str(path))
        if not src.isNull():
            pm = src.scaled(
                _OPTIONS_COG_PX,
                _OPTIONS_COG_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    _OPTIONS_COG_CACHE = pm
    return pm


class OptionsCogButton(QPushButton):
    """Addons repository-settings control painted with UI-OptionsButton art."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("OptionsCogButton")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(GLUE_ROW_MENU_W, GLUE_ROW_H)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(
            "QPushButton#OptionsCogButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
        )
        self._icon = _options_cog_pixmap()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            rect = self.rect()
            icon = self._icon
            if icon.isNull():
                painter.setPen(Qt.GlobalColor.yellow)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "⚙")
                painter.end()
                return
            if self.isDown():
                painter.setOpacity(0.75)
            elif self.underMouse():
                painter.setOpacity(1.0)
            else:
                painter.setOpacity(0.92)
            x = rect.center().x() - icon.width() // 2
            y = rect.center().y() - icon.height() // 2 + (1 if self.isDown() else 0)
            painter.drawPixmap(x, y, icon)
            painter.end()


        finally:
            if painter.isActive():
                painter.end()

def _pass_icon_pixmap(pressed: bool) -> QPixmap:
    """Bundled WoW GroupLoot Pass art for the addon-row Remove control."""
    key = "down" if pressed else "up"
    hit = _PASS_CACHE.get(key)
    if hit is not None:
        return hit
    name = _PASS_DOWN if pressed else _PASS_UP
    path = theme_file(name)
    if not path.is_file():
        path = _PASS_EXTERNAL / name
    pm = QPixmap()
    if path.is_file():
        src = QPixmap(str(path))
        if not src.isNull():
            pm = src.scaled(
                _PASS_ICON_PX,
                _PASS_ICON_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    _PASS_CACHE[key] = pm
    return pm


def _refresh_icon_pixmap() -> QPixmap:
    """Bundled WoW UI-RefreshButton, scaled for the addons row reinstall control."""
    global _REFRESH_CACHE
    if _REFRESH_CACHE is not None:
        return _REFRESH_CACHE
    path = theme_file(_REFRESH_BTN)
    if not path.is_file():
        path = _REFRESH_EXTERNAL
    pm = QPixmap()
    if path.is_file():
        src = QPixmap(str(path))
        if not src.isNull():
            pm = src.scaled(
                _OPTIONS_COG_PX,
                _OPTIONS_COG_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    _REFRESH_CACHE = pm
    return pm


class RefreshReinstallButton(QPushButton):
    """Addons-row Reinstall control painted with UI-RefreshButton art."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RefreshReinstallButton")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(GLUE_ROW_MENU_W, GLUE_ROW_H)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(
            "QPushButton#RefreshReinstallButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
        )
        self._icon = _refresh_icon_pixmap()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            rect = self.rect()
            icon = self._icon
            if icon.isNull():
                painter.setPen(Qt.GlobalColor.yellow)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "↻")
                painter.end()
                return
            if self.isDown():
                painter.setOpacity(0.75)
            elif self.underMouse():
                painter.setOpacity(1.0)
            else:
                painter.setOpacity(0.92)
            x = rect.center().x() - icon.width() // 2
            y = (
                rect.center().y()
                - icon.height() // 2
                + _REINSTALL_ICON_Y_NUDGE
                + (1 if self.isDown() else 0)
            )
            painter.drawPixmap(x, y, icon)
            painter.end()


        finally:
            if painter.isActive():
                painter.end()

def _folder_icon_pixmap() -> QPixmap:
    """Bundled folder glyph, scaled to the same size as Reinstall / Remove."""
    global _FOLDER_CACHE
    if _FOLDER_CACHE is not None:
        return _FOLDER_CACHE
    path = theme_file(_FOLDER_BTN)
    pm = QPixmap()
    if path.is_file():
        src = QPixmap(str(path))
        if not src.isNull():
            pm = src.scaled(
                _OPTIONS_COG_PX,
                _OPTIONS_COG_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    _FOLDER_CACHE = pm
    return pm


class FolderOpenButton(QPushButton):
    """Addons-row control that opens the installed addon directory."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FolderOpenButton")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(GLUE_ROW_MENU_W, GLUE_ROW_H)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(
            "QPushButton#FolderOpenButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
        )
        self.setToolTip("Open addon folder")
        self.setAccessibleName("Open addon folder")
        self._icon = _folder_icon_pixmap()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            rect = self.rect()
            icon = self._icon
            if icon.isNull():
                painter.setPen(Qt.GlobalColor.yellow)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "[]")
                painter.end()
                return
            if self.isDown():
                painter.setOpacity(0.75)
            elif self.underMouse():
                painter.setOpacity(1.0)
            else:
                painter.setOpacity(0.92)
            x = rect.center().x() - icon.width() // 2
            y = rect.center().y() - icon.height() // 2 + (1 if self.isDown() else 0)
            painter.drawPixmap(x, y, icon)
            painter.end()


        finally:
            if painter.isActive():
                painter.end()

class OpenGitButton(QPushButton):
    """Open in Git — row/dialog plates, or a compact inline control beside the title."""

    def __init__(self, parent: QWidget | None = None, *, plate: str = "row"):
        super().__init__(parent)
        self.setObjectName("OpenGitButton")
        self._plate = plate
        if plate == "dialog":
            self._side = GLUE_BTN_H
            hit = GLUE_BTN_H
        elif plate == "inline":
            self._side = _OPEN_GIT_INLINE_PX
            hit = _OPEN_GIT_INLINE_HIT
        else:
            self._side = GLUE_ROW_H
            hit = GLUE_ROW_H
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # Beat global QPushButton { padding: 8px 14px } and any leftover text-button
        # min-width so the hit box stays art-sized (setFixedSize alone loses to QSS).
        self.setStyleSheet(
            "QPushButton#OpenGitButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0px;"
            "  margin: 0px;"
            f"  min-width: {hit}px;"
            f"  max-width: {hit}px;"
            f"  min-height: {hit}px;"
            f"  max-height: {hit}px;"
            "  color: transparent;"
            "}"
        )
        self.setFixedSize(hit, hit)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("Open the repository in your browser")
        self.setAccessibleName("Open in Git")

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        super().mousePressEvent(event)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            rect = self.rect()
            chrome_down = bool(self.isDown())
            press_dy = _UPDATE_PRESS_DY if chrome_down else 0
            icon = open_git_icon_pixmap(
                pressed=chrome_down,
                disabled=not self.isEnabled(),
                side=self._side,
            )
            if icon.isNull():
                painter.setPen(Qt.GlobalColor.yellow)
                align = (
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                    if self._plate == "inline"
                    else Qt.AlignmentFlag.AlignCenter
                )
                painter.drawText(
                    rect.adjusted(0, press_dy, 0, press_dy),
                    align,
                    "Git",
                )
                painter.end()
                return
            if chrome_down:
                painter.setOpacity(0.85)
            elif self.underMouse():
                painter.setOpacity(1.0)
            else:
                painter.setOpacity(0.94)
            # Inline: left-align glyph so any accidental hit-box slack sits to the right
            # of the icon (next to the name), not as empty space before it.
            if self._plate == "inline":
                x = 0
            else:
                x = rect.center().x() - icon.width() // 2
            y = rect.center().y() - icon.height() // 2 + press_dy
            painter.drawPixmap(x, y, icon)
            painter.end()


        finally:
            if painter.isActive():
                painter.end()

class _DownloadGlyph(QWidget):
    """Small downward-arrow tray used next to the addon download count."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._muted = False

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            color = QColor("#7a6e58" if self._muted else "#C4A35A")
            painter.setPen(QPen(color, 1.4))
            painter.setBrush(color)
            w, h = self.width(), self.height()
            # Shaft
            painter.drawLine(w // 2, 1, w // 2, h - 5)
            # Arrow head
            painter.drawPolygon(
                [
                    QPoint(2, h - 6),
                    QPoint(w - 2, h - 6),
                    QPoint(w // 2, h - 2),
                ]
            )
            painter.end()


        finally:
            if painter.isActive():
                painter.end()

class AddonDownloadCount(QWidget):
    """Download glyph + latest-release count to the right of Open-in-Git."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AddonDownloadCount")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self._icon = _DownloadGlyph(self)
        self._label = QLabel("—", self)
        self._label.setObjectName("AddonDownloadCountLabel")
        self._label.setStyleSheet("color: #C4A35A; font-size: 11px; font-weight: 600;")
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.hide()

    def apply_entry(self, entry: dict[str, Any] | None) -> None:
        from ichalaunch.addons.release_downloads import (
            download_badge_text,
            download_badge_tooltip,
        )

        text = download_badge_text(entry)
        if not text:
            self.hide()
            return
        muted = text == "—"
        self._icon.set_muted(muted)
        self._label.setText(text)
        color = "#7a6e58" if muted else "#C4A35A"
        self._label.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: 600;")
        self.setToolTip(download_badge_tooltip(entry))
        self.show()


class OpenLinkButton(OpenGitButton):
    """Square Open control — same chrome as Open in Git, for project/download pages."""

    def __init__(self, parent: QWidget | None = None, *, plate: str = "row"):
        super().__init__(parent, plate=plate)
        self.setToolTip("Open the project page in your browser")
        self.setAccessibleName("Open")


class PassRemoveButton(QPushButton):
    """Compact square Remove control: GroupLoot Pass icon only (no plate chrome)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PassRemoveButton")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(GLUE_ROW_MENU_W, GLUE_ROW_H)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(
            "QPushButton#PassRemoveButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
        )

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            rect = self.rect()
            icon = _pass_icon_pixmap(self.isDown())
            if icon.isNull():
                painter.setPen(Qt.GlobalColor.white)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "×")
            else:
                if self.isDown():
                    painter.setOpacity(0.75)
                elif self.underMouse():
                    painter.setOpacity(1.0)
                else:
                    painter.setOpacity(0.92)
                x = rect.center().x() - icon.width() // 2
                y = rect.center().y() - icon.height() // 2 + (1 if self.isDown() else 0)
                painter.drawPixmap(x, y, icon)
            painter.end()


        finally:
            if painter.isActive():
                painter.end()

def _knockout_spellbook_well(pm: QPixmap) -> QPixmap:
    """Treat BLP-style black wells as transparent so the glyph sits on the wash."""
    if pm.isNull():
        return pm
    img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() == 0:
                continue
            if c.red() + c.green() + c.blue() <= _SPELLBOOK_WELL_SUM:
                c.setAlpha(0)
                img.setPixelColor(x, y, c)
    return QPixmap.fromImage(img)


def _spellbook_page_pixmap(
    direction: str,
    *,
    pressed: bool,
    side: int = _SPELLBOOK_ICON_PX,
    art: str = "up",
) -> QPixmap:
    """Bundled WoW spellbook Next/Prev icons (Up/Down, or Disabled for Home)."""
    direction = "prev" if direction == "prev" else "next"
    art = "disabled" if art == "disabled" else "up"
    key = (direction, bool(pressed), int(side), art)
    hit = _SPELLBOOK_CACHE.get(key)
    if hit is not None:
        return hit
    if art == "disabled":
        name = _SPELLBOOK_PREV_DISABLED if direction == "prev" else _SPELLBOOK_NEXT_DISABLED
    elif direction == "prev":
        name = _SPELLBOOK_PREV_DOWN if pressed else _SPELLBOOK_PREV_UP
    else:
        name = _SPELLBOOK_NEXT_DOWN if pressed else _SPELLBOOK_NEXT_UP
    path = theme_file(name)
    if not path.is_file():
        path = _SPELLBOOK_EXTERNAL / name
    pm = QPixmap()
    if path.is_file():
        src = QPixmap(str(path))
        if not src.isNull():
            if art == "disabled":
                src = _knockout_spellbook_well(src)
            pm = src.scaled(
                side,
                side,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    _SPELLBOOK_CACHE[key] = pm
    return pm


class SpellbookPageButton(QPushButton):
    """Prev/Next painted with UI-SpellbookIcon page art (Up/Down or Disabled)."""

    def __init__(
        self,
        direction: str,
        parent: QWidget | None = None,
        *,
        art: str = "up",
    ):
        super().__init__(parent)
        self._direction = "prev" if direction == "prev" else "next"
        self._art = "disabled" if art == "disabled" else "up"
        self.setObjectName("SpellbookPageButton")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(_SPELLBOOK_PAGE_H, _SPELLBOOK_PAGE_H)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(
            "QPushButton#SpellbookPageButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
        )
        if self._direction == "prev":
            self.setToolTip("Previous page")
            self.setAccessibleName("Previous page")
        else:
            self.setToolTip("Next page")
            self.setAccessibleName("Next page")

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        super().mousePressEvent(event)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            rect = self.rect()
            pressed = bool(self.isDown())
            icon = _spellbook_page_pixmap(self._direction, pressed=pressed, art=self._art)
            if icon.isNull():
                painter.setPen(Qt.GlobalColor.yellow)
                label = "◀" if self._direction == "prev" else "▶"
                painter.drawText(
                    rect.adjusted(0, _UPDATE_PRESS_DY if pressed else 0, 0, 0),
                    Qt.AlignmentFlag.AlignCenter,
                    label,
                )
                painter.end()
                return
            if not self.isEnabled():
                painter.setOpacity(_SPELLBOOK_DISABLED_OPACITY)
            elif self._art == "disabled":
                if pressed:
                    painter.setOpacity(_SPELLBOOK_PRESS_OPACITY)
                elif self.underMouse():
                    painter.setOpacity(_SPELLBOOK_HOVER_OPACITY)
                else:
                    painter.setOpacity(_SPELLBOOK_IDLE_OPACITY)
            elif pressed:
                painter.setOpacity(_SPELLBOOK_PRESS_OPACITY)
            elif self.underMouse():
                painter.setOpacity(_SPELLBOOK_HOVER_OPACITY)
            else:
                painter.setOpacity(0.94)
            press_dy = _UPDATE_PRESS_DY if pressed else 0
            x = rect.center().x() - icon.width() // 2
            y = rect.center().y() - icon.height() // 2 + press_dy
            painter.drawPixmap(x, y, icon)
            painter.end()


            # Addon-row Update: square glowing plate (side == Reinstall height) — arrow only.
            # Chrome matches GluePanelButton("Reinstall", … height=GLUE_ROW_H); glow may
            # enlarge the widget around that plate (AlignVCenter keeps plates flush).
        finally:
            if painter.isActive():
                painter.end()
_UPDATE_BTN_SIDE = GLUE_ROW_H  # chrome W == H == Reinstall button height
_UPDATE_ARROW = "UI-MicroStream-Yellow.PNG"
_UPDATE_ARROW_EXTERNAL = Path(r"F:\wow-ui-textures\Buttons") / _UPDATE_ARROW
_UPDATE_ARROW_PX = 18  # fits centered in a 28² plate
_UPDATE_ARROW_CACHE: QPixmap | None = None
_INSTALL_ARROW_CACHE: QPixmap | None = None
_UPDATE_ARROW_Y_NUDGE = 0  # true geometric center in the square plate
# Content nudge when chrome is depressed (same as GluePanelButton / GlueCombo).
_UPDATE_PRESS_DY = 1


def _row_update_arrow_pixmap() -> QPixmap:
    """Home UPDATE chevron (MicroStream flipped up), scaled for GLUE_ROW_H plates."""
    global _UPDATE_ARROW_CACHE, _INSTALL_ARROW_CACHE
    if _UPDATE_ARROW_CACHE is not None:
        return _UPDATE_ARROW_CACHE
    path = theme_file(_UPDATE_ARROW)
    if not path.is_file():
        path = _UPDATE_ARROW_EXTERNAL
    pm = QPixmap()
    if path.is_file():
        src = QPixmap(str(path))
        if not src.isNull():
            flipped = QPixmap.fromImage(src.toImage().mirrored(False, True))
            pm = flipped.scaled(
                _UPDATE_ARROW_PX,
                _UPDATE_ARROW_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    _UPDATE_ARROW_CACHE = pm
    _INSTALL_ARROW_CACHE = None
    return pm


def _row_install_arrow_pixmap() -> QPixmap:
    """Available-row INSTALL chevron (MicroStream down) — same scale as Update."""
    global _INSTALL_ARROW_CACHE
    if _INSTALL_ARROW_CACHE is not None:
        return _INSTALL_ARROW_CACHE
    up = _row_update_arrow_pixmap()
    if up.isNull():
        _INSTALL_ARROW_CACHE = up
        return up
    _INSTALL_ARROW_CACHE = QPixmap.fromImage(up.toImage().mirrored(False, True))
    return _INSTALL_ARROW_CACHE


class AddonRowInstallButton(QPushButton):
    """Square Install control matching Update plate height — arrow only, no glow."""

    install_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AddonRowInstallButton")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(_UPDATE_BTN_SIDE, _UPDATE_BTN_SIDE)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(
            "QPushButton#AddonRowInstallButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
        )
        self._icon = _row_install_arrow_pixmap()
        glue_row_square_chrome(pressed=False, role="primary", side=_UPDATE_BTN_SIDE)
        glue_row_square_chrome(pressed=True, role="primary", side=_UPDATE_BTN_SIDE)
        self.setToolTip("Install this addon")
        self.setAccessibleName("Install")
        self.clicked.connect(self.install_clicked.emit)

    def _arrow_icon(self) -> QPixmap:
        return _row_install_arrow_pixmap()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        super().mousePressEvent(event)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            rect = self.rect()
            chrome_down = bool(self.isDown())
            pm = glue_row_square_chrome(
                pressed=chrome_down,
                role="primary",
                disabled=not self.isEnabled(),
                side=_UPDATE_BTN_SIDE,
            )
            if pm.isNull():
                painter.setPen(QColor("#94836a"))
                painter.setBrush(QColor("#6b4a1e"))
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
            else:
                painter.drawPixmap(rect, pm)

            press_dy = _UPDATE_PRESS_DY if chrome_down else 0
            icon = self._arrow_icon()
            if icon.isNull():
                painter.setPen(QColor("#F1C22D"))
                painter.drawText(
                    rect.adjusted(0, press_dy + _UPDATE_ARROW_Y_NUDGE, 0, press_dy + _UPDATE_ARROW_Y_NUDGE),
                    Qt.AlignmentFlag.AlignCenter,
                    "↓",
                )
            else:
                if not self.isEnabled():
                    painter.setOpacity(0.45)
                ax = rect.center().x() - icon.width() // 2
                ay = (
                    rect.center().y()
                    - icon.height() // 2
                    + _UPDATE_ARROW_Y_NUDGE
                    + press_dy
                )
                painter.drawPixmap(ax, ay, icon)
                painter.setOpacity(1.0)
            painter.end()


        finally:
            if painter.isActive():
                painter.end()

class AddonRowUpdateButton(QPushButton):
    """Square glowing Update control matching Reinstall plate height.

    Chrome plate is ``GLUE_ROW_H`` × ``GLUE_ROW_H`` (same as Reinstall height).
    CheckButtonGlow is hole-matched to that plate and drawn in margins around
    the chrome (widget expands to the glow pixmap). Row uses AlignVCenter so
    the plate stays flush with Reinstall. Arrow-only — Never Update lives in
    the settings cog dialog.
    """

    update_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("AddonRowUpdateButton")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(
            "QPushButton#AddonRowUpdateButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
        )
        self._chrome_w = _UPDATE_BTN_SIDE
        self._chrome_h = _UPDATE_BTN_SIDE
        # Hole-matched glow (independent W/H); widget == glow so ring is not
        # crushed into the chrome — same pattern as GluePanelButton(glowing).
        self._glow_pm = check_button_glow_for_plate(self._chrome_w, self._chrome_h)
        gw = (
            self._glow_pm.width()
            if not self._glow_pm.isNull()
            else self._chrome_w + 16
        )
        gh = (
            self._glow_pm.height()
            if not self._glow_pm.isNull()
            else self._chrome_h + 16
        )
        self.setFixedSize(max(self._chrome_w, gw), max(self._chrome_h, gh))
        self._glow_pulse = 0.0
        self._glow_timer = QTimer(self)
        self._glow_timer.setInterval(40)
        self._glow_timer.timeout.connect(self._tick_glow_pulse)
        self._glow_timer.start()
        self._icon = _row_update_arrow_pixmap()
        # Compat alias for AddonRow wiring.
        self.update_btn = self
        glue_row_square_chrome(pressed=False, role="primary", side=_UPDATE_BTN_SIDE)
        glue_row_square_chrome(pressed=True, role="primary", side=_UPDATE_BTN_SIDE)
        self.setToolTip("Update available")
        self.setAccessibleName("Update")
        self.clicked.connect(self.update_clicked.emit)

    def _chrome_rect(self) -> QRect:
        # Center plate in glow margins so L/R/T/B insets stay equal.
        return QRect(
            (self.width() - self._chrome_w) // 2,
            (self.height() - self._chrome_h) // 2,
            self._chrome_w,
            self._chrome_h,
        )

    def _tick_glow_pulse(self) -> None:
        self._glow_pulse = (self._glow_pulse + 0.10) % (2 * math.pi)
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        super().mousePressEvent(event)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self.update()

    def _paint_glow(self, painter: QPainter) -> None:
        """Draw hole-matched glow at native size (centered; not stretched)."""
        if self._glow_pm.isNull() or not self.isEnabled():
            return
        wave = 0.5 + 0.5 * math.sin(self._glow_pulse)
        if self.underMouse() or self.isDown():
            opacity = 0.95
        else:
            opacity = 0.40 + 0.55 * wave
        painter.setOpacity(opacity)
        glow = self._glow_pm
        dest = self.rect()
        if glow.width() != dest.width() or glow.height() != dest.height():
            x = dest.center().x() - glow.width() // 2
            y = dest.center().y() - glow.height() // 2
            painter.drawPixmap(x, y, glow)
        else:
            painter.drawPixmap(dest, glow)
        painter.setOpacity(1.0)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

            self._paint_glow(painter)
            rect = self._chrome_rect()
            chrome_down = bool(self.isDown())
            pm = glue_row_square_chrome(
                pressed=chrome_down,
                role="primary",
                disabled=not self.isEnabled(),
                side=_UPDATE_BTN_SIDE,
            )
            if pm.isNull():
                painter.setPen(QColor("#94836a"))
                painter.setBrush(QColor("#6b4a1e"))
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
            else:
                painter.drawPixmap(rect, pm)

            press_dy = _UPDATE_PRESS_DY if chrome_down else 0

            # Update arrow — centered in the square plate.
            icon = self._icon
            if icon.isNull():
                painter.setPen(QColor("#F1C22D"))
                painter.drawText(
                    rect.adjusted(0, press_dy + _UPDATE_ARROW_Y_NUDGE, 0, press_dy + _UPDATE_ARROW_Y_NUDGE),
                    Qt.AlignmentFlag.AlignCenter,
                    "↑",
                )
            else:
                if not self.isEnabled():
                    painter.setOpacity(0.45)
                ax = rect.center().x() - icon.width() // 2
                ay = (
                    rect.center().y()
                    - icon.height() // 2
                    + _UPDATE_ARROW_Y_NUDGE
                    + press_dy
                )
                painter.drawPixmap(ax, ay, icon)
                painter.setOpacity(1.0)

            painter.end()


            # Back-compat alias for previews / older imports.
        finally:
            if painter.isActive():
                painter.end()
AddonRowUpdateSplit = AddonRowUpdateButton


# Turtle WoW custom-addon badge (legacy ichalaunch raven — not the launcher icon).
# Mark detection lives in ``ichalaunch.addons.catalog`` (``turtle_custom``).
_TURTLE_BADGE_PX = 18
_TURTLE_BADGE_TIP = "Turtle WoW custom addon"
_turtle_badge_pm: QPixmap | None = None


def _turtle_wow_badge_pixmap() -> QPixmap:
    """Cached Addons-tab raven icon scaled for AddonRow height."""
    global _turtle_badge_pm
    if _turtle_badge_pm is not None:
        return _turtle_badge_pm

    pm = QPixmap()
    for name in ("ichalaunch.png", "ichalaunch.ico"):
        path = theme_file(name)
        if not path.exists():
            continue
        src = QPixmap(str(path))
        if src.isNull():
            continue
        pm = src.scaled(
            _TURTLE_BADGE_PX,
            _TURTLE_BADGE_PX,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        break
    _turtle_badge_pm = pm
    return pm


def format_updated_stamp(meta: dict[str, Any] | None) -> str | None:
    """Human date from installed_addons / installed_mods metadata."""
    if not meta:
        return None
    raw = meta.get("updated_at") or meta.get("installed_at") or meta.get("commit_date")
    if raw is None or raw == "":
        return None
    try:
        if isinstance(raw, (int, float)):
            dt = datetime.fromtimestamp(float(raw), tz=timezone.utc)
        else:
            text = str(raw).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            if "T" in text:
                dt = datetime.fromisoformat(text)
            else:
                dt = datetime.strptime(text[:10], "%Y-%m-%d")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%b %d, %Y")
    except (TypeError, ValueError, OSError):
        return None
def status_with_stamp(base: str, meta: dict[str, Any] | None = None) -> str:
    """Append · date for Up to date rows when metadata has a stamp."""
    if not base.startswith("Up to date"):
        return base
    stamp = format_updated_stamp(meta)
    return f"{base} · {stamp}" if stamp else base


def mod_author(mod: dict[str, Any] | None) -> str | None:
    """Best-effort creator credit for a client mod catalog entry."""
    if not mod:
        return None
    explicit = str(mod.get("author") or "").strip()
    if explicit:
        return explicit
    mid = str(mod.get("id") or "")
    if mid.startswith("hd_patch"):
        return "Project Reforged"
    src = mod.get("source") if isinstance(mod.get("source"), dict) else {}
    addon_src = mod.get("addon_source") if isinstance(mod.get("addon_source"), dict) else {}
    repo_url = github_repo_browse_url(
        mod.get("repo"),
        mod.get("repo_url"),
        mod.get("repository"),
        mod.get("github"),
        mod.get("url"),
        src.get("repo"),
        src.get("url"),
        addon_src.get("repo"),
        addon_src.get("url"),
    )
    if repo_url:
        try:
            from ichalaunch.addons.github import parse_github_url

            parsed = parse_github_url(repo_url)
            if parsed and parsed.owner:
                return parsed.owner
        except Exception:  # noqa: BLE001
            pass
        parts = repo_url.replace("https://github.com/", "").split("/")
        if parts and parts[0]:
            return parts[0]
    for raw in (src.get("url"), addon_src.get("url")):
        text = str(raw or "").strip().lower()
        if "raw.githubusercontent.com/" in text:
            try:
                path = urlparse(str(raw)).path.strip("/").split("/")
                if len(path) >= 1:
                    return path[0]
            except Exception:  # noqa: BLE001
                continue
    return None


def addon_fork_label(entry: dict[str, Any] | None) -> str:
    """Display owner/repo for an addon catalog or installed row."""
    if not entry:
        return ""
    base = ""
    for raw in (
        entry.get("repo"),
        entry.get("url"),
        entry.get("repository"),
    ):
        url = git_repo_browse_url(raw)
        if not url:
            continue
        try:
            from ichalaunch.addons.github import parse_github_url
            from ichalaunch.addons.gitlab import parse_gitlab_url

            gl = parse_gitlab_url(url)
            if gl:
                base = f"{gl.owner}/{gl.repo}"
                break
            parsed = parse_github_url(url)
            if parsed:
                base = f"{parsed.owner}/{parsed.repo}"
                break
        except Exception:  # noqa: BLE001
            pass
        tail = url.replace("https://github.com/", "").replace("https://gitlab.com/", "").strip("/")
        if tail:
            base = tail.split("/")[0:2] and "/".join(tail.split("/")[0:2]) or tail
            break
    if not base:
        base = str(entry.get("label") or "").strip()
    if entry.get("archived"):
        return f"{base} (archived)" if base else "(archived)"
    return base


def fork_combo_label(entry: dict[str, Any] | None) -> str:
    """Fork picker combo text; prefers parsed repo name and archived suffix."""
    if not entry:
        return "?"
    label = addon_fork_label(entry)
    if label:
        return label
    return str(entry.get("label") or entry.get("repo") or "?")


def addon_version_label(
    entry: dict[str, Any] | None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Installed or catalog version string for addon rows."""
    meta = meta if isinstance(meta, dict) else {}
    entry = entry if isinstance(entry, dict) else {}

    def _format(raw: Any) -> str:
        from ichalaunch.addons.git_refs import (
            extract_semver_label,
            is_preferred_release_alias,
            is_usable_release_tag,
            is_version_tag,
            looks_like_timestamp_label,
        )

        text = str(raw or "").strip()
        if not text or looks_like_timestamp_label(text) or is_preferred_release_alias(text):
            return ""
        extracted = extract_semver_label(text)
        if extracted and (
            " " in text
            or "/" in text
            or text.lower().endswith((".zip", ".rar", ".7z"))
        ):
            return extracted
        if not is_usable_release_tag(text) and not is_version_tag(text):
            return extracted
        if not text.lower().startswith("v") and text[:1].isdigit():
            return f"v{text}"
        return text

    for raw in (
        meta.get("version"),
        meta.get("tag"),
        entry.get("tag"),
        entry.get("pin_release"),
    ):
        label = _format(raw)
        if label:
            return label
    try:
        from ichalaunch.addons.github import catalog_pin_tag, parse_entry_owner_repo
        from ichalaunch.addons.tip_index import lookup_display_version

        pin = catalog_pin_tag(entry)
        label = _format(pin)
        if label:
            return label
        parsed = parse_entry_owner_repo(entry)
        if parsed:
            return lookup_display_version(parsed[0], parsed[1])
    except Exception:  # noqa: BLE001
        pass
    return ""
# Hub for Turtle WoW client tweaks/patches that ship without a dedicated repo.
TURTLEWOW_MODS_HUB = "https://github.com/RetroCro/TurtleWoW-Mods"


class _BrowseUrlCheckThread(QThread):
    """Background HEAD/GET probe so Open in Git never blocks the UI."""

    finished_check = Signal(str, bool)

    def __init__(self, url: str):
        # Never parent to a row widget — sync row delete while running aborts Qt.
        super().__init__()
        self._url = url

    def run(self) -> None:
        from ichalaunch.addons.github import github_url_reachable

        if self.isInterruptionRequested():
            return
        ok = False
        try:
            ok = bool(github_url_reachable(self._url))
        except Exception:  # noqa: BLE001
            ok = False
        # Cancelled probes must not touch row widgets (may already be destroyed).
        if self.isInterruptionRequested():
            return
        self.finished_check.emit(self._url, ok)


# Cancelled browse-URL probes stay here until QThread.finished. Dropping the last
# Python ref while run() is still active aborts Qt (WER 0xC0000409, no traceback).
_ORPHAN_GIT_URL_THREADS: list[QThread] = []


def _reap_orphan_git_url_thread(thread: QThread) -> None:
    """Drop an orphaned probe after its thread has actually terminated."""
    try:
        while thread in _ORPHAN_GIT_URL_THREADS:
            _ORPHAN_GIT_URL_THREADS.remove(thread)
    except ValueError:
        pass
    try:
        if not _shiboken_is_valid(thread):
            return
        # finished has fired; wait() returns once run() has fully unwound.
        thread.wait(1)
        thread.deleteLater()
    except RuntimeError:
        return


def drain_orphan_git_url_threads(wait_ms: int = 5000) -> None:
    """Block briefly so cancelled probes finish (tests / shutdown)."""
    deadline = max(0, int(wait_ms))
    for thread in list(_ORPHAN_GIT_URL_THREADS):
        try:
            if not _shiboken_is_valid(thread):
                _reap_orphan_git_url_thread(thread)
                continue
            if thread.isRunning():
                thread.requestInterruption()
                thread.wait(deadline)
            _reap_orphan_git_url_thread(thread)
        except RuntimeError:
            continue


def cancel_git_url_checks(owner: QObject, *, wait_ms: int = 0) -> None:
    """Invalidate in-flight Open-in-Git probes before a row widget is torn down.

    Disconnects UI slots and bumps the generation so late results are ignored.
    Still-running QThreads are moved to ``_ORPHAN_GIT_URL_THREADS`` — they must
    not be GC'd while ``run()`` executes (that aborts Qt). Optional *wait_ms*
    blocks briefly; pagination prefers orphan+proceed over a long wait.
    """
    gen = int(getattr(owner, "_git_url_check_gen", 0) or 0) + 1
    setattr(owner, "_git_url_check_gen", gen)
    setattr(owner, "_git_url_pending", None)
    threads: list[QThread] = list(getattr(owner, "_git_url_threads", []) or [])
    setattr(owner, "_git_url_threads", [])
    still_running: list[QThread] = []
    for thread in threads:
        try:
            thread.finished_check.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            if not _shiboken_is_valid(thread):
                continue
            if thread.isRunning():
                thread.requestInterruption()
                still_running.append(thread)
            else:
                thread.deleteLater()
        except RuntimeError:
            continue
    for thread in still_running:
        if thread not in _ORPHAN_GIT_URL_THREADS:
            _ORPHAN_GIT_URL_THREADS.append(thread)
            try:
                thread.finished.connect(
                    lambda *_args, t=thread: _reap_orphan_git_url_thread(t)
                )
            except (RuntimeError, TypeError):
                pass
        # finished may have fired between isRunning() and connect — reap now.
        try:
            if _shiboken_is_valid(thread) and not thread.isRunning():
                _reap_orphan_git_url_thread(thread)
        except RuntimeError:
            pass
    if wait_ms > 0:
        for thread in still_running:
            try:
                if _shiboken_is_valid(thread) and thread.isRunning():
                    thread.wait(int(wait_ms))
                if _shiboken_is_valid(thread) and not thread.isRunning():
                    _reap_orphan_git_url_thread(thread)
            except RuntimeError:
                continue



def apply_open_git_visibility(
    button: QPushButton,
    url: str | None,
    owner: QObject,
    *,
    defer: bool = False,  # noqa: ARG001 — kept for call-site compatibility
) -> None:
    """Show *Open in Git* when a browse URL is known — no network probe.

    Click opens the known URL via the existing handlers. *defer* is ignored
    (formerly gated async HEAD checks that raced with pagination).
    """
    text = (url or "").strip() or None
    try:
        if not _shiboken_is_valid(button):
            return
    except RuntimeError:
        return
    if not text:
        try:
            button.setVisible(False)
            button.setToolTip("No git repository link")
        except RuntimeError:
            return
        return
    try:
        button.setToolTip(f"Open {text}")
        button.setVisible(True)
    except RuntimeError:
        return
    setattr(owner, "_git_url_deferred", text)


def codeberg_repo_browse_url(*candidates: Any) -> str | None:
    """Best-effort https://codeberg.org/owner/repo from catalog/meta fields."""
    for raw in candidates:
        if not raw:
            continue
        text = str(raw).strip()
        if not text:
            continue
        try:
            parsed = urlparse(text)
        except Exception:  # noqa: BLE001
            continue
        host = (parsed.hostname or "").lower()
        if host not in {"codeberg.org", "www.codeberg.org"}:
            continue
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(parts) >= 2:
            return f"https://codeberg.org/{parts[0]}/{parts[1]}"
    return None


def git_repo_browse_url(*candidates: Any) -> str | None:
    """Best-effort GitHub, GitLab.com, or Codeberg browse URL from catalog fields.

    GitLab URLs stay on gitlab.com — they are never rewritten as GitHub.
    Bare ``owner/repo`` tokens still resolve as GitHub (existing convention).
    """
    from ichalaunch.addons.gitlab import gitlab_browse_url, parse_gitlab_url

    found = codeberg_repo_browse_url(*candidates)
    if found:
        return found
    for raw in candidates:
        if not raw:
            continue
        text = str(raw).strip()
        if not text:
            continue
        try:
            gl = parse_gitlab_url(text)
        except Exception:  # noqa: BLE001
            gl = None
        if gl:
            return gitlab_browse_url(gl.owner, gl.repo)
    return github_repo_browse_url(*candidates)


def github_repo_browse_url(*candidates: Any) -> str | None:
    """Best-effort https://github.com/owner/repo from catalog/meta fields."""
    for raw in candidates:
        if not raw:
            continue
        text = str(raw).strip()
        if not text:
            continue
        if text.count("/") == 1 and "://" not in text and " " not in text:
            return f"https://github.com/{text}"
        try:
            from ichalaunch.addons.github import parse_github_url
            parsed = parse_github_url(text)
            if parsed:
                return f"https://github.com/{parsed.owner}/{parsed.repo}"
        except Exception:  # noqa: BLE001
            pass
        lower = text.lower()
        # github.com/... and raw.githubusercontent.com/owner/repo/...
        if "github.com" in lower or "githubusercontent.com" in lower:
            try:
                path = urlparse(text).path.strip("/")
                parts = [p for p in path.split("/") if p]
                if len(parts) >= 2:
                    return f"https://github.com/{parts[0]}/{parts[1]}"
            except Exception:  # noqa: BLE001
                continue
    return None


def mod_git_url(mod: dict[str, Any] | None) -> str | None:
    """Public git page for a client mod — per-item repo, else TurtleWoW-Mods hub.

    HD Graphics rows intentionally omit Open-in-Git; they expose only the
    Project Reforged ``info_url`` via :func:`mod_open_url`.
    """
    if not mod:
        return None
    if str(mod.get("category") or "") == "HD Graphics":
        return None
    src = mod.get("source") if isinstance(mod.get("source"), dict) else {}
    found = git_repo_browse_url(
        mod.get("repo_url"),
        mod.get("repo"),
        mod.get("info_url"),
        mod.get("github"),
        mod.get("url"),
        mod.get("repository"),
        (src or {}).get("repo"),
        (src or {}).get("url"),
        (src or {}).get("github"),
    )
    if found:
        return found
    # Catalog / ecosystem entries without a dedicated repo still link to the hub.
    return TURTLEWOW_MODS_HUB


def mod_open_url(mod: dict[str, Any] | None) -> str | None:
    """Non-Git project/download page when it differs from the row's git link."""
    if not mod:
        return None
    info = str(mod.get("info_url") or "").strip()
    if not info:
        return None
    # HD Graphics: always prefer the project page (no Open-in-Git companion).
    if str(mod.get("category") or "") == "HD Graphics":
        return info
    git = mod_git_url(mod)
    if git and info.rstrip("/").lower() == git.rstrip("/").lower():
        return None
    return info


def open_url_in_browser(url: str) -> bool:
    text = (url or "").strip()
    if not text:
        return False
    return bool(QDesktopServices.openUrl(QUrl(text)))


def open_local_path(path: Path | str) -> bool:
    """Open a local file or folder in the OS file manager (Explorer / xdg-open / Finder)."""
    try:
        target = Path(path)
    except (TypeError, ValueError):
        return False
    if not target.exists():
        return False
    resolved = str(target.resolve())
    if QDesktopServices.openUrl(QUrl.fromLocalFile(resolved)):
        return True
    if sys.platform == "win32":
        try:
            os.startfile(resolved)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, resolved], close_fds=True)
        return True
    except OSError:
        return False


# https://discord.com/users/<id>, discordapp.com, or discord://-/users/<id>
_DISCORD_USER_RE = re.compile(
    r"(?:https?://(?:www\.)?discord(?:app)?\.com/users/|discord://-/users/)(\d+)",
    re.IGNORECASE,
)


def discord_user_id_from_url(url: str) -> str | None:
    """Return a Discord snowflake from a profile URL, or None if not a user link."""
    text = (url or "").strip()
    if not text:
        return None
    if text.isdigit():
        return text
    m = _DISCORD_USER_RE.search(text)
    return m.group(1) if m else None


def _protocol_registered(scheme: str) -> bool | None:
    """True/False when we can check Windows registry; None means try anyway."""
    scheme = (scheme or "").strip().lower()
    if not scheme:
        return False
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    for root, path in (
        (winreg.HKEY_CURRENT_USER, rf"Software\Classes\{scheme}\shell\open\command"),
        (winreg.HKEY_CLASSES_ROOT, rf"{scheme}\shell\open\command"),
        (winreg.HKEY_CLASSES_ROOT, scheme),
    ):
        try:
            with winreg.OpenKey(root, path):
                return True
        except OSError:
            continue
    return False


def _discord_protocol_registered() -> bool | None:
    """True/False when we can check Windows registry; None means try anyway."""
    return _protocol_registered("discord")


def _vesktop_executable() -> Path | None:
    """Locate a Vesktop (or legacy VencordDesktop) install, if present."""
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        program_files_x86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        candidates = (
            local / "vesktop" / "vesktop.exe",
            local / "Programs" / "vesktop" / "vesktop.exe",
            local / "VencordDesktop" / "VencordDesktop.exe",
            program_files / "vesktop" / "vesktop.exe",
            program_files / "Vesktop" / "vesktop.exe",
            program_files_x86 / "vesktop" / "vesktop.exe",
            program_files_x86 / "Vesktop" / "vesktop.exe",
        )
        for path in candidates:
            if path.is_file():
                return path
        return None
    if sys.platform == "darwin":
        mac = Path("/Applications/Vesktop.app/Contents/MacOS/Vesktop")
        return mac if mac.is_file() else None
    which = shutil.which("vesktop")
    return Path(which) if which else None


def _process_named_running(image_name: str) -> bool:
    """Best-effort check for a running process (Windows tasklist; else False)."""
    if sys.platform != "win32":
        return False
    name = (image_name or "").strip()
    if not name:
        return False
    try:
        kwargs: dict[str, Any] = {
            "args": ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            "capture_output": True,
            "text": True,
            "timeout": 5,
            "check": False,
        }
        # Avoid a flash console window when launched from the GUI.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if creationflags:
            kwargs["creationflags"] = creationflags
        completed = subprocess.run(**kwargs)
        out = (completed.stdout or "") + (completed.stderr or "")
        return name.lower() in out.lower()
    except (OSError, subprocess.SubprocessError):
        return False


# Local CDP port used when we cold-start Vesktop so warm clicks can open profiles
# in the existing window (Vesktop's second-instance handler only focuses).
_VESKTOP_CDP_PORT = 9229
_DISCORD_IPC_CLIENT_ID = "122178054565183488"  # unused public-style id for handshake


def _launch_app_with_url(exe: Path, url: str, *extra_args: str) -> bool:
    """Start ``exe`` with ``url`` (and optional Chromium flags) as argv."""
    args = [url, *[a for a in extra_args if a]]
    try:
        # Prefer Qt detach so the launcher is not tied to Vesktop's lifetime.
        ok, _pid = QProcess.startDetached(str(exe), args)
        if ok:
            return True
    except (TypeError, RuntimeError, OSError):
        pass
    try:
        kwargs: dict[str, Any] = {"close_fds": True}
        if sys.platform == "win32":
            flags = 0
            for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
                flags |= getattr(subprocess, name, 0)
            if flags:
                kwargs["creationflags"] = flags
        subprocess.Popen([str(exe), *args], **kwargs)
        return True
    except OSError:
        return False


def _windows_open_uri(uri: str) -> bool:
    """Open a URI via the OS shell (protocol handler), ignoring registry probes."""
    text = (uri or "").strip()
    if not text:
        return False
    if sys.platform == "win32":
        try:
            os.startfile(text)  # type: ignore[attr-defined]
            return True
        except OSError:
            pass
        try:
            import ctypes

            # > 32 means ShellExecute started the association successfully.
            rc = int(ctypes.windll.shell32.ShellExecuteW(None, "open", text, None, None, 1))
            return rc > 32
        except (AttributeError, OSError, ValueError):
            pass
    return bool(QDesktopServices.openUrl(QUrl(text)))


def _discord_ipc_paths() -> list[str]:
    if sys.platform == "win32":
        return [rf"\\.\pipe\discord-ipc-{i}" for i in range(10)]
    bases: list[str] = []
    for key in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
        val = os.environ.get(key)
        if val:
            bases.append(val)
    bases.append("/tmp")
    paths: list[str] = []
    for base in bases:
        for i in range(10):
            paths.append(str(Path(base) / f"discord-ipc-{i}"))
    return paths


def _discord_ipc_encode(opcode: int, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("<II", opcode, len(data)) + data


def _discord_ipc_read(sock: socket.socket, timeout: float = 2.0) -> tuple[int, dict[str, Any]] | None:
    sock.settimeout(timeout)
    try:
        hdr = sock.recv(8)
        if len(hdr) < 8:
            return None
        opcode, length = struct.unpack("<II", hdr)
        body = b""
        while len(body) < length:
            chunk = sock.recv(length - len(body))
            if not chunk:
                break
            body += chunk
        if len(body) < length:
            return None
        return opcode, json.loads(body.decode("utf-8"))
    except (OSError, TimeoutError, json.JSONDecodeError):
        return None


def _discord_ipc_open_user(user_id: str) -> bool:
    """Ask a running Discord/Vesktop arRPC server to open ``users/<id>``."""
    uid = (user_id or "").strip()
    if not uid.isdigit():
        return False
    payload = {
        "cmd": "DEEP_LINK",
        "args": {"type": "FEATURES", "params": {"path": f"users/{uid}"}},
        "nonce": str(uuid.uuid4()),
    }
    for path in _discord_ipc_paths():
        if sys.platform == "win32":
            try:
                pipe = open(path, "r+b", buffering=0)
            except OSError:
                continue
            try:
                pipe.write(_discord_ipc_encode(0, {"v": 1, "client_id": _DISCORD_IPC_CLIENT_ID}))
                pipe.flush()
                hdr = pipe.read(8)
                if len(hdr) < 8:
                    continue
                _op, length = struct.unpack("<II", hdr)
                body = pipe.read(length)
                if len(body) < length:
                    continue
                pipe.write(_discord_ipc_encode(1, payload))
                pipe.flush()
                hdr = pipe.read(8)
                if len(hdr) < 8:
                    continue
                _op, length = struct.unpack("<II", hdr)
                body = pipe.read(length)
                msg = json.loads(body.decode("utf-8")) if body else {}
                return msg.get("evt") is None
            except (OSError, json.JSONDecodeError, struct.error):
                continue
            finally:
                try:
                    pipe.close()
                except OSError:
                    pass
            continue

        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(path)
            sock.sendall(_discord_ipc_encode(0, {"v": 1, "client_id": _DISCORD_IPC_CLIENT_ID}))
            if _discord_ipc_read(sock) is None:
                continue
            sock.sendall(_discord_ipc_encode(1, payload))
            resp = _discord_ipc_read(sock)
            if resp is None:
                return False
            _opcode, msg = resp
            return msg.get("evt") is None
        except OSError:
            continue
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    return False


def _ws_recv_frame(sock: socket.socket) -> bytes:
    """Read one unmasked WebSocket binary/text frame (client role)."""
    hdr = sock.recv(2)
    if len(hdr) < 2:
        raise OSError("short ws header")
    b1, b2 = hdr[0], hdr[1]
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        ext = sock.recv(2)
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = sock.recv(8)
        length = struct.unpack("!Q", ext)[0]
    mask = sock.recv(4) if masked else b""
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            break
        data += chunk
    if masked and mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    if opcode == 0x8:
        raise OSError("ws closed")
    if opcode == 0x9:  # ping -> pong
        sock.sendall(bytes([0x8A, len(data)]) + data)
        return _ws_recv_frame(sock)
    return data


def _ws_send_text(sock: socket.socket, text: str) -> None:
    data = text.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x81])
    n = len(data)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(header + masked)


def _cdp_connect(ws_url: str) -> socket.socket:
    """Minimal WebSocket client handshake for Chromium CDP."""
    parsed = urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock = socket.create_connection((host, port), timeout=2.0)
    sock.settimeout(3.0)
    sock.sendall(req.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise OSError("CDP handshake closed")
        buf += chunk
    status_line = buf.split(b"\r\n", 1)[0].decode("ascii", "replace")
    if "101" not in status_line:
        sock.close()
        raise OSError(f"CDP handshake failed: {status_line}")
    expected = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
    ).decode("ascii")
    if expected not in buf.decode("latin-1", "replace"):
        # Some Chromium builds omit echoing checks under allow-origins; still proceed if 101.
        pass
    return sock


def _cdp_evaluate(ws_url: str, expression: str, await_promise: bool = True) -> Any:
    sock = _cdp_connect(ws_url)
    try:
        msg_id = 1
        _ws_send_text(
            sock,
            json.dumps(
                {
                    "id": msg_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": await_promise,
                    },
                }
            ),
        )
        while True:
            raw = _ws_recv_frame(sock)
            data = json.loads(raw.decode("utf-8"))
            if data.get("id") != msg_id:
                continue
            result = (data.get("result") or {}).get("result") or {}
            if "exceptionDetails" in (data.get("result") or {}):
                return None
            return result.get("value")
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _vesktop_cdp_page_ws_urls(ports: tuple[int, ...] | None = None) -> list[str]:
    urls: list[str] = []
    for port in ports or (_VESKTOP_CDP_PORT, 9222, 9223):
        try:
            with urlopen(f"http://127.0.0.1:{port}/json", timeout=0.4) as resp:
                tabs = json.loads(resp.read().decode("utf-8"))
        except (OSError, TimeoutError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(tabs, list):
            continue
        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            if tab.get("type") != "page":
                continue
            ws = tab.get("webSocketDebuggerUrl")
            if isinstance(ws, str) and ws:
                urls.append(ws)
    return urls


def _vesktop_cdp_open_user(user_id: str) -> bool:
    """Open a user profile in a running Vesktop via Chromium CDP + Vencord helpers."""
    uid = (user_id or "").strip()
    if not uid.isdigit():
        return False
    # Discord's openUserProfileModal expects an options object, not a bare id string.
    expression = f"""(async () => {{
  try {{
    if (!window.Vencord) return 'no-vencord';
    const mod = Vencord.Webpack.findByProps('openUserProfileModal');
    if (mod && mod.openUserProfileModal) {{
      const guildId = Vencord.Webpack.Common.SelectedGuildStore?.getGuildId?.() ?? undefined;
      const channelId = Vencord.Webpack.Common.SelectedChannelStore?.getChannelId?.() ?? undefined;
      mod.openUserProfileModal({{
        userId: '{uid}',
        guildId,
        channelId,
        analyticsLocation: {{ page: guildId ? 'Guild Channel' : 'DM Channel', section: 'Profile Popout' }}
      }});
      return 'ok-modal';
    }}
    const {{ FluxDispatcher, UserUtils, SelectedChannelStore }} = Vencord.Webpack.Common;
    if (UserUtils && UserUtils.fetchUser) await UserUtils.fetchUser('{uid}');
    FluxDispatcher.dispatch({{
      type: 'USER_PROFILE_MODAL_OPEN',
      userId: '{uid}',
      channelId: SelectedChannelStore?.getChannelId?.(),
      analyticsLocation: 'IchaLaunch'
    }});
    return 'ok-dispatch';
  }} catch (e) {{
    return 'err:' + String(e && e.message || e);
  }}
}})()"""
    for ws_url in _vesktop_cdp_page_ws_urls():
        try:
            value = _cdp_evaluate(ws_url, expression, await_promise=True)
        except OSError:
            continue
        if isinstance(value, str) and value.startswith("ok"):
            return True
    return False


def open_discord_user_profile(url_or_id: str) -> bool:
    """Open a Discord user profile in a desktop client when possible, else the browser.

    Order (warm Vesktop included):
    1. Chromium CDP ``openUserProfileModal`` when Vesktop was started with remote
       debugging (we pass this on cold launch).
    2. Discord IPC ``DEEP_LINK`` / arRPC when a ``discord-ipc-*`` pipe is listening.
    3. Launch Vesktop with ``discord://-/users/<id>`` even if already running
       (cold start navigates; warm builds at least focus the window).
    4. OS ``discord://`` via ``os.startfile`` / ShellExecute / Qt.
    5. HTTPS profile URL in the browser.
    """
    text = (url_or_id or "").strip()
    if not text:
        return False
    user_id = discord_user_id_from_url(text)
    if not user_id:
        return open_url_in_browser(text)
    https_url = f"https://discord.com/users/{user_id}"
    deep_url = f"discord://-/users/{user_id}"

    # Warm path that actually opens the profile modal without reloading Discord.
    if _vesktop_cdp_open_user(user_id):
        return True

    if _discord_ipc_open_user(user_id):
        return True

    vesktop = _vesktop_executable()
    if vesktop is not None:
        running = _process_named_running(vesktop.name)
        if running:
            # Newer Vesktop still ignores second-instance argv for navigation, but
            # launching with the deep link focuses the window; worth trying first.
            if _launch_app_with_url(vesktop, deep_url):
                # If CDP becomes available after focus, try once more (no-op usually).
                if _vesktop_cdp_open_user(user_id):
                    return True
            if _windows_open_uri(deep_url):
                if _vesktop_cdp_open_user(user_id):
                    return True
            # Fall through to browser — focus-only is worse than a working profile.
        else:
            # Cold start: argv discord:// is handled by loadUrl. Also enable CDP so
            # later warm clicks can open profiles inside the existing window.
            if _launch_app_with_url(
                vesktop,
                deep_url,
                f"--remote-debugging-port={_VESKTOP_CDP_PORT}",
                "--remote-allow-origins=*",
            ):
                return True

    if _windows_open_uri(deep_url):
        if _vesktop_cdp_open_user(user_id):
            return True
        registered = _discord_protocol_registered()
        # Only trust bare protocol success as terminal when a handler is registered
        # and Vesktop was not already our target (avoids silent no-ops).
        if registered and vesktop is None:
            return True

    return open_url_in_browser(https_url)


class FlowLayout(QLayout):
    """Simple left-to-right wrapping layout for chip rows."""
    def __init__(self, parent=None, margin: int = 0, spacing: int = 8):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)
    def count(self) -> int:
        return len(self._items)
    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None
    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None
    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)
    def hasHeightForWidth(self) -> bool:
        return True
    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)
    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)
    def sizeHint(self) -> QSize:
        return self.minimumSize()
    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size
    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_h = 0
        space = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + space
            if next_x - space > effective.right() and line_h:
                x = effective.x()
                y = y + line_h + space
                next_x = x + hint.width() + space
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()
class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setProperty("class", "Card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 14, 16, 14)
        self._layout.setSpacing(8)
    @property
    def body(self) -> QVBoxLayout:
        return self._layout
MOD_EDIT_LOCKED_TIP = (
    "Close World of Warcraft for this client folder "
    "(WoW.exe / VanillaFixes.exe) to change client mods."
)


class ModCheckRow(QWidget):
    """Compact row: [checkbox] Name [▸] [created by] [Open] [Git] — status [Update] [Reinstall].

    Version and description stay collapsed behind the caret until expanded.
    Optional *contains* line (e.g. bundled companions) stays visible beneath the title.
    Open-link / Open-in-Git sit immediately after the author tag (tight spacing).
    """
    toggled = Signal(str, bool)
    update_clicked = Signal(str)
    reinstall_clicked = Signal(str)
    open_git_clicked = Signal(str)
    open_link_clicked = Signal(str)
    settings_clicked = Signal(str)
    def __init__(
        self,
        mod_id: str,
        title: str,
        description: str,
        checked: bool = False,
        *,
        author: str | None = None,
        contains: str | None = None,
        version: str | None = None,
        has_settings: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        # Layout child (not a QListWidget item) — must stay visible. AddonRow uses
        # WA_DontShowOnScreen + hide() because lists reveal via _reveal_item_widgets;
        # CLIENT has no such path, so those flags left an empty category panel.
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.mod_id = mod_id
        self._full_desc = (description or "").replace("\n", " ").strip()
        self._version = (version or "").strip()
        self._desc_expanded = False
        self._git_url: str | None = None
        self._open_url: str | None = None
        self._editing_locked = False
        self._feature_locked = False
        self._feature_lock_tip = ""
        self._nested = False
        self._update_detail = ""
        self.setObjectName("ModCheckRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.cb = ThemeCheckBox("", self)
        self.cb.setFixedSize(22, 22)
        self.cb.setChecked(checked)
        self.cb.toggled.connect(lambda v: self.toggled.emit(self.mod_id, v))
        # Name cluster: name → details caret → author → open/git (~6–8px visual gap).
        name_cluster = QHBoxLayout()
        name_cluster.setContentsMargins(0, 0, 0, 0)
        name_cluster.setSpacing(6)
        name_lbl = QLabel(title, self)
        name_lbl.setObjectName("ModRowName")
        name_lbl.setWordWrap(False)
        name_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self._name_lbl = name_lbl
        self.desc_toggle = QPushButton("▸", self)
        self.desc_toggle.setObjectName("DescToggle")
        self.desc_toggle.setFlat(True)
        self.desc_toggle.setFixedSize(18, 22)
        apply_open_hand(self.desc_toggle)
        self.desc_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.desc_toggle.setToolTip("Show details")
        self.desc_toggle.clicked.connect(self._toggle_desc)
        self.author_lbl = QLabel("", self)
        self.author_lbl.setObjectName("Muted")
        self.author_lbl.setWordWrap(False)
        self.author_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        if author:
            self.author_lbl.setText(f"created by {author}")
        else:
            self.author_lbl.setVisible(False)
        self.open_link_btn = OpenLinkButton(self, plate="inline")
        self.open_link_btn.setVisible(False)
        self.open_link_btn.clicked.connect(self._emit_open_link)
        self.open_git_btn = OpenGitButton(self, plate="inline")
        self.open_git_btn.setVisible(False)
        self.open_git_btn.clicked.connect(self._emit_open_git)
        self.pending_badge = UpdateAlertBadge(self)
        name_cluster.addWidget(name_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        name_cluster.addWidget(self.pending_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        name_cluster.addWidget(self.desc_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        if author:
            name_cluster.addWidget(self.author_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        name_cluster.addWidget(self.open_link_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        name_cluster.addWidget(self.open_git_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        sep2 = QLabel("—", self)
        sep2.setObjectName("Muted")
        self.status_lbl = QLabel("", self)
        self.status_lbl.setObjectName("Muted")
        self.status_lbl.setWordWrap(False)
        self.status_lbl.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.update_btn = AddonRowUpdateButton(self)
        self.update_btn.setVisible(False)
        self.update_btn.update_clicked.connect(
            lambda: self.update_clicked.emit(self.mod_id)
        )
        self.reinstall_btn = RefreshReinstallButton(self)
        self.reinstall_btn.setVisible(False)
        self.reinstall_btn.setToolTip("Re-download and overwrite installed files")
        self.reinstall_btn.clicked.connect(lambda: self.reinstall_clicked.emit(self.mod_id))
        self.settings_btn: OptionsCogButton | None = None
        action_host = QWidget(self)
        action_host.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        action_l = QHBoxLayout(action_host)
        action_l.setContentsMargins(0, 0, 0, 0)
        action_l.setSpacing(_ADDON_ROW_ACTION_GAP)
        action_l.addWidget(self.update_btn)
        action_l.addWidget(self.reinstall_btn)
        if has_settings:
            btn_set = OptionsCogButton(self)
            btn_set.setToolTip("Configure Vanilla Tweaks patches")
            btn_set.clicked.connect(lambda: self.settings_clicked.emit(self.mod_id))
            action_l.addWidget(btn_set)
            self.settings_btn = btn_set
        row.addWidget(self.cb, 0)
        row.addLayout(name_cluster, 0)
        row.addStretch(1)
        row.addWidget(sep2, 0)
        row.addWidget(self.status_lbl, 0)
        row.addWidget(action_host, 0, Qt.AlignmentFlag.AlignBottom)
        self.contains_lbl = QLabel(self)
        self.contains_lbl.setObjectName("Muted")
        self.contains_lbl.setWordWrap(True)
        contains_text = (contains or "").strip()
        if contains_text:
            self.contains_lbl.setText(contains_text)
            self.contains_lbl.setVisible(True)
        else:
            self.contains_lbl.clear()
            self.contains_lbl.setVisible(False)
        self.version_lbl = QLabel(self)
        self.version_lbl.setObjectName("Muted")
        self.version_lbl.setWordWrap(False)
        self.version_lbl.setVisible(False)
        self.desc_lbl = QLabel(self)
        self.desc_lbl.setObjectName("Muted")
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setVisible(False)
        outer.addLayout(row)
        outer.addWidget(self.contains_lbl)
        outer.addWidget(self.version_lbl)
        outer.addWidget(self.desc_lbl)
        self._apply_desc()
    def _has_dropdown(self) -> bool:
        return bool(self._full_desc or self._version)
    def _toggle_desc(self) -> None:
        self._desc_expanded = not self._desc_expanded
        self._apply_desc()
        self.updateGeometry()
    def _apply_desc(self) -> None:
        if not self._has_dropdown():
            self.version_lbl.clear()
            self.version_lbl.setVisible(False)
            self.desc_lbl.clear()
            self.desc_lbl.setVisible(False)
            self.desc_toggle.setVisible(False)
            return
        self.desc_toggle.setVisible(True)
        if self._desc_expanded:
            if self._version:
                self.version_lbl.setText(f"Version {self._version}")
                self.version_lbl.setVisible(True)
            else:
                self.version_lbl.clear()
                self.version_lbl.setVisible(False)
            if self._full_desc:
                self.desc_lbl.setText(self._full_desc)
                self.desc_lbl.setVisible(True)
            else:
                self.desc_lbl.clear()
                self.desc_lbl.setVisible(False)
            self.desc_toggle.setText("▾")
            self.desc_toggle.setToolTip("Hide details")
        else:
            self.version_lbl.clear()
            self.version_lbl.setVisible(False)
            self.desc_lbl.clear()
            self.desc_lbl.setVisible(False)
            self.desc_toggle.setText("▸")
            self.desc_toggle.setToolTip("Show details")
    def _emit_open_git(self) -> None:
        self.open_git_clicked.emit(self.mod_id)

    def _emit_open_link(self) -> None:
        self.open_link_clicked.emit(self.mod_id)

    def set_git_url(self, url: str | None) -> None:
        self._git_url = (url or "").strip() or None
        apply_open_git_visibility(self.open_git_btn, self._git_url, self)

    def set_open_url(self, url: str | None) -> None:
        self._open_url = (url or "").strip() or None
        if self._open_url:
            self.open_link_btn.setVisible(True)
            self.open_link_btn.setToolTip(f"Open {self._open_url}")
        else:
            self.open_link_btn.setVisible(False)
            self.open_link_btn.setToolTip("Open the project page in your browser")

    def set_version(self, version: str | None) -> None:
        self._version = (version or "").strip()
        self.version_lbl.setToolTip(f"Version {self._version}" if self._version else "")
        self._apply_desc()

    def set_update_available(self, available: bool, detail: str = "") -> None:
        self.update_btn.setVisible(available)
        self._update_detail = (detail or "Update available") if available else ""
        if available:
            self.update_btn.setToolTip(self._update_detail)
        self._sync_editing_lock()

    def set_reinstall_visible(self, visible: bool) -> None:
        self.reinstall_btn.setVisible(visible)
        self._sync_editing_lock()

    def set_editing_locked(self, locked: bool) -> None:
        """Grey out the checkbox while WoW.exe / VanillaFixes.exe is running."""
        self._editing_locked = bool(locked)
        self._sync_editing_lock()

    def set_nested(self, nested: bool) -> None:
        """Indent this row as a child option under the previous catalog row."""
        self._nested = bool(nested)
        extra = 22 if self._nested else 0
        self.layout().setContentsMargins(8 + extra, 6, 8, 6)

    def set_feature_locked(self, locked: bool, tip: str = "") -> None:
        """Grey the checkbox for a feature-level lock (does not disable Apply actions)."""
        self._feature_locked = bool(locked)
        self._feature_lock_tip = tip if locked else ""
        self._sync_editing_lock()

    def _sync_editing_lock(self) -> None:
        game_locked = self._editing_locked
        feature_locked = self._feature_locked
        self.cb.setEnabled(not (game_locked or feature_locked))
        if game_locked:
            self.cb.setToolTip(MOD_EDIT_LOCKED_TIP)
        elif feature_locked:
            self.cb.setToolTip(self._feature_lock_tip)
        else:
            self.cb.setToolTip("")
        self.update_btn.setEnabled(not game_locked)
        self.reinstall_btn.setEnabled(not game_locked)
        if self.settings_btn is not None:
            self.settings_btn.setEnabled(not game_locked)
            self.settings_btn.setToolTip(
                MOD_EDIT_LOCKED_TIP if game_locked else "Configure Vanilla Tweaks patches"
            )
        if game_locked:
            self.update_btn.setToolTip(MOD_EDIT_LOCKED_TIP)
            self.reinstall_btn.setToolTip(MOD_EDIT_LOCKED_TIP)
            return
        if self.update_btn.isVisible() and self._update_detail:
            self.update_btn.setToolTip(self._update_detail)
        if self.reinstall_btn.toolTip() == MOD_EDIT_LOCKED_TIP:
            self.reinstall_btn.setToolTip("Re-download and overwrite installed files")

    def set_pending_change(self, pending: bool) -> None:
        """Show the Adventure Guide alert on this row when Apply has work."""
        pending = bool(pending)
        self.pending_badge.setVisible(pending)

    def flash_highlight(self, ms: int = 2200) -> None:
        """Brief gold flash so the user can find a newly selected/matched row."""
        self.setProperty("flashHighlight", True)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

        def _clear() -> None:
            self.setProperty("flashHighlight", False)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

        QTimer.singleShot(max(400, int(ms)), _clear)


class AddonRow(QWidget):
    install_clicked = Signal(dict)
    update_clicked = Signal(dict)
    reinstall_clicked = Signal(dict)
    remove_clicked = Signal(str)
    open_git_clicked = Signal(dict)
    preview_clicked = Signal(dict)
    settings_clicked = Signal(dict)
    loaded_toggled = Signal(dict, bool)
    fork_changed = Signal(dict)
    height_changed = Signal()
    def __init__(
        self,
        entry: dict,
        status: str = "available",
        *,
        modules: list[str] | None = None,
        never_update: bool = False,
        loaded: bool = True,
        meta: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        # Off-screen BEFORE any child buttons — an unparented/on-screen QWidget is a HWND.
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.hide()
        self.entry = entry
        self._meta = meta if isinstance(meta, dict) else {}
        self._modules = [m for m in (modules or []) if m]
        self._modules_expanded = False
        self._never_update = bool(never_update)
        self._update_available = status.startswith("Update")
        self._status_text = status
        self.open_git_btn: OpenGitButton | None = None
        self.folder_btn: FolderOpenButton | None = None
        self.download_count: AddonDownloadCount | None = None
        self.settings_btn: OptionsCogButton | None = None
        self.load_cb: ThemeCheckBox | None = None
        self._loaded = bool(loaded)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 2, 8, 2)
        root.setSpacing(2)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        # Small gap between name → modules caret → Open-in-Git (~6–8px visual).
        name_row.setSpacing(6)
        is_installed = (
            status in ("Installed", "Not checked", "—", "Never update")
            or status.startswith("Up to date")
            or status.startswith("Update")
            or status.startswith("Never update")
        )
        if is_installed:
            from ichalaunch.addons.loadstate import UNLOAD_TOOLTIP

            self.load_cb = ThemeCheckBox("", self)
            self.load_cb.setFixedSize(22, 22)
            self.load_cb.setToolTip(UNLOAD_TOOLTIP)
            self.load_cb.blockSignals(True)
            self.load_cb.setChecked(self._loaded)
            self.load_cb.blockSignals(False)
            self.load_cb.toggled.connect(self._on_loaded_toggled)
            name_row.addWidget(self.load_cb, 0, Qt.AlignmentFlag.AlignVCenter)
        if is_turtle_wow_custom_addon(entry):
            badge_pm = _turtle_wow_badge_pixmap()
            if not badge_pm.isNull():
                badge = QLabel(self)
                badge.setPixmap(badge_pm)
                badge.setFixedSize(badge_pm.size())
                badge.setToolTip(_TURTLE_BADGE_TIP)
                name_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        name = QLabel(entry.get("name", "?"), self)
        name.setStyleSheet("font-weight: 600; color: #F1C22D;")
        name.setWordWrap(False)
        name.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        name_row.addWidget(name, 0)
        self._name_lbl = name
        git_url = git_repo_browse_url(
            entry.get("repo"),
            entry.get("url"),
            entry.get("repository"),
        )
        self._git_url = git_url
        # Cluster: [name] [modules caret?] [Open-in-Git] → stretch.
        self.modules_toggle = QPushButton("▸", self)
        self.modules_toggle.setObjectName("DescToggle")
        self.modules_toggle.setFlat(True)
        self.modules_toggle.setFixedSize(18, 20)
        apply_open_hand(self.modules_toggle)
        self.modules_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        n_mod = len(self._modules)
        self.modules_toggle.setToolTip(
            f"Show {n_mod} nested module{'s' if n_mod != 1 else ''}" if n_mod else ""
        )
        self.modules_toggle.clicked.connect(self._toggle_modules)
        if n_mod > 0:
            name_row.addWidget(self.modules_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            self.modules_toggle.setVisible(False)
            self.modules_toggle.setParent(self)
        if git_url:
            btn_git = OpenGitButton(self, plate="inline")
            btn_git.clicked.connect(lambda: self.open_git_clicked.emit(entry))
            name_row.addWidget(btn_git, 0, Qt.AlignmentFlag.AlignVCenter)
            self.open_git_btn = btn_git
            apply_open_git_visibility(btn_git, git_url, self)
        self.download_count = AddonDownloadCount(self)
        self.download_count.apply_entry(entry)
        name_row.addWidget(self.download_count, 0, Qt.AlignmentFlag.AlignVCenter)
        name_row.addStretch(1)
        layout.addLayout(name_row, 1)
        self.status_lbl = QLabel(status, self)
        self._apply_status_style(status)
        layout.addWidget(self.status_lbl)
        if is_installed:
            show_update = self._update_available and not self._never_update
            # Square glowing Update plate: centered arrow only.
            self._update_btn_widget = AddonRowUpdateButton(self)
            self._update_btn = self._update_btn_widget.update_btn
            self._update_btn_widget.update_clicked.connect(self._on_update_clicked)
            self._update_btn_widget.setVisible(show_update)
            layout.addWidget(self._update_btn_widget, 0, Qt.AlignmentFlag.AlignVCenter)
            self.folder_btn = FolderOpenButton(self)
            self.folder_btn.clicked.connect(self._on_open_folder)
            self.reinstall_btn: RefreshReinstallButton | None = None
            action_host = QWidget(self)
            action_host.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            action_l = QHBoxLayout(action_host)
            action_l.setContentsMargins(0, 0, 0, 0)
            action_l.setSpacing(_ADDON_ROW_ACTION_GAP)
            action_l.addWidget(self.folder_btn)
            if git_url or entry.get("source") == "github" or entry.get("tag"):
                btn_ri = RefreshReinstallButton(self)
                btn_ri.setToolTip(
                    "Re-download and overwrite installed files "
                    "(also clears Never Update for this addon)"
                )
                btn_ri.clicked.connect(lambda: self.reinstall_clicked.emit(entry))
                action_l.addWidget(btn_ri)
                self.reinstall_btn = btn_ri
            btn_r = PassRemoveButton(self)
            btn_r.setToolTip("Remove this addon")
            btn_r.clicked.connect(
                lambda: self.remove_clicked.emit(entry.get("folder") or entry.get("name"))
            )
            action_l.addWidget(btn_r)
            if git_url:
                btn_set = OptionsCogButton(self)
                btn_set.setToolTip(
                    "Repository settings — fork, version, Never update, and README"
                )
                btn_set.clicked.connect(lambda: self.settings_clicked.emit(entry))
                action_l.addWidget(btn_set)
                self.settings_btn = btn_set
            layout.addWidget(action_host, 0, Qt.AlignmentFlag.AlignBottom)
            self._refresh_never_update_ui()
        else:
            self._update_btn_widget = None
            self._update_btn = None
            self.folder_btn = None
            self.reinstall_btn = None
            btn = AddonRowInstallButton(self)
            btn.install_clicked.connect(lambda: self.install_clicked.emit(entry))
            layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(layout)
        self.modules_panel = QLabel(self)
        self.modules_panel.setObjectName("Muted")
        self.modules_panel.setWordWrap(True)
        self.modules_panel.setVisible(False)
        root.addWidget(self.modules_panel)

    def preferred_height(self) -> int:
        return max(48, self.sizeHint().height())

    def _on_loaded_toggled(self, checked: bool) -> None:
        self._loaded = bool(checked)
        self.entry["loaded"] = self._loaded
        self.loaded_toggled.emit(self.entry, self._loaded)

    def set_loaded(self, loaded: bool) -> None:
        self._loaded = bool(loaded)
        self.entry["loaded"] = self._loaded
        if self.load_cb is None:
            return
        self.load_cb.blockSignals(True)
        self.load_cb.setChecked(self._loaded)
        self.load_cb.blockSignals(False)

    def _on_update_clicked(self) -> None:
        self.update_clicked.emit(self.entry)

    def _on_open_folder(self) -> None:
        from ichalaunch.addons.loadstate import addon_disk_path

        folder = str(self.entry.get("folder") or self.entry.get("name") or "").strip()
        dest = addon_disk_path(folder) if folder else None
        if dest is None or not dest.is_dir():
            log.warning("Addon folder missing for %s", folder or "?")
            return
        if not open_local_path(dest):
            log.warning("Could not open addon folder: %s", dest)

    def _refresh_never_update_ui(self) -> None:
        show_update = self._update_available and not self._never_update
        if self._never_update:
            self.status_lbl.setText("Never update")
            self.status_lbl.setStyleSheet("color: #7a6e58;")
        else:
            self.status_lbl.setText(self._status_text)
            self._apply_status_style(self._status_text)
        btn = getattr(self, "_update_btn_widget", None)
        if btn is not None:
            btn.setVisible(show_update)
        elif self._update_btn is not None:
            self._update_btn.setVisible(show_update)

    def refresh_download_count(self, entry: dict[str, Any] | None = None) -> None:
        """Update the latest-release download badge after a catalog/fork change."""
        if self.download_count is None:
            return
        if entry is not None:
            self.entry = entry
        self.download_count.apply_entry(self.entry)

    def apply_status(self, status: str, *, never_update: bool | None = None) -> None:
        """Patch labels/buttons in place (no recreate — avoids HWND flashes)."""
        if never_update is not None:
            self._never_update = bool(never_update)
        self._status_text = status
        self._update_available = status.startswith("Update")
        try:
            self._refresh_never_update_ui()
        except RuntimeError:
            return

    def _apply_status_style(self, status: str) -> None:
        if status.startswith("Update"):
            self.status_lbl.setStyleSheet("color: #F1C22D;")
        elif status.startswith("Up to date") or status == "Installed":
            self.status_lbl.setStyleSheet("color: #7c5cc4;")
        elif status.startswith("Never update"):
            self.status_lbl.setStyleSheet("color: #7a6e58;")
        else:
            self.status_lbl.setObjectName("Muted")
            self.status_lbl.setStyleSheet("")

    def _toggle_modules(self) -> None:
        self._modules_expanded = not self._modules_expanded
        if self._modules_expanded and self._modules:
            lines = " · ".join(self._modules)
            self.modules_panel.setText(f"Modules: {lines}")
            self.modules_panel.setVisible(True)
            self.modules_toggle.setText("▾")
            self.modules_toggle.setToolTip("Hide nested modules")
        else:
            self.modules_panel.clear()
            self.modules_panel.setVisible(False)
            self.modules_toggle.setText("▸")
            n_mod = len(self._modules)
            self.modules_toggle.setToolTip(
                f"Show {n_mod} nested module{'s' if n_mod != 1 else ''}" if n_mod else ""
            )
        self.updateGeometry()
        self.height_changed.emit()
