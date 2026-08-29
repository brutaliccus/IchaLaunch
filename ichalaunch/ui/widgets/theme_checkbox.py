"""RavenCraft-themed checkbox with WoW quickslot indicator art.

Uses QAbstractButton (not QCheckBox) so QStyleSheetStyle never paints
QCheckBox::indicator:hover — that path calls QWidget.paintEngine() and
spams ``Paint device returned engine == 0`` under a translucent main window.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import QAbstractButton, QWidget

from ichalaunch.core.paths import theme_file

_FALLBACK_DIR = Path(r"F:\wow-ui-textures\Buttons")

_EMPTY = "UI-EmptySlot.PNG"
_DEPRESS = "UI-Quickslot-Depress.PNG"
_CHECK = "UI-CheckBox-Check.PNG"
_CHECK_DISABLED = "UI-CheckBox-Check-Disabled.PNG"

_INDICATOR_PX = 22
_LABEL_SPACING = 10
# Cropped check glyph scaled to this fraction of the EmptySlot so tips meet
# the frame's inner rim (transparent corners let it sit larger than the old gold fill).
_CHECK_FIT_SCALE = 0.94

_GOLD = QColor("#F1C22D")

_CACHE: dict[str, QPixmap] = {}


def _resolve_asset(name: str) -> Path | None:
    bundled = theme_file("checkboxes", name)
    if bundled.is_file():
        return bundled
    fallback = _FALLBACK_DIR / name
    if fallback.is_file():
        return fallback
    return None


def _load_raw(name: str) -> QPixmap:
    path = _resolve_asset(name)
    if path is None:
        return QPixmap()
    pm = QPixmap(str(path))
    return pm if not pm.isNull() else QPixmap()


def _opaque_bounds(pm: QPixmap, alpha_min: int = 20) -> QRect:
    if pm.isNull():
        return QRect()
    img = pm.toImage()
    w, h = img.width(), img.height()
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if img.pixelColor(x, y).alpha() >= alpha_min:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if max_x < min_x:
        return QRect(0, 0, w, h)
    return QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def _normalize_to_square(pm: QPixmap, side: int) -> QPixmap:
    if pm.isNull() or side <= 0:
        return QPixmap()
    bounds = _opaque_bounds(pm)
    cropped = pm.copy(bounds) if bounds.isValid() else pm
    return cropped.scaled(
        side,
        side,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _prepare_check(pm: QPixmap, target_side: int) -> QPixmap:
    """Crop transparent padding and scale check to fit target_side (KeepAspectRatio)."""
    if pm.isNull() or target_side <= 0:
        return QPixmap()
    bounds = _opaque_bounds(pm)
    cropped = pm.copy(bounds) if bounds.isValid() else pm
    return cropped.scaled(
        target_side,
        target_side,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _scaled_indicator(pm: QPixmap) -> QPixmap:
    if pm.isNull():
        return QPixmap()
    return pm.scaled(
        _INDICATOR_PX,
        _INDICATOR_PX,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _assets() -> tuple[QPixmap, QPixmap, QPixmap, QPixmap]:
    key = "normalized_chrome_check_v2_disabled"
    if key in _CACHE:
        return (
            _CACHE["empty"],
            _CACHE["depress"],
            _CACHE["checked"],
            _CACHE["checked_disabled"],
        )

    raw_empty = _load_raw(_EMPTY)
    raw_depress = _load_raw(_DEPRESS)
    raw_check = _load_raw(_CHECK)
    raw_check_off = _load_raw(_CHECK_DISABLED)
    sides = [pm.width() for pm in (raw_empty, raw_depress) if not pm.isNull()]
    common = max(sides) if sides else 64

    empty = _scaled_indicator(_normalize_to_square(raw_empty, common))
    depress = _scaled_indicator(_normalize_to_square(raw_depress, common))
    check_target = max(1, int(round(_INDICATOR_PX * _CHECK_FIT_SCALE)))
    checked = _prepare_check(raw_check, check_target)
    checked_disabled = _prepare_check(raw_check_off, check_target)

    _CACHE["empty"] = empty
    _CACHE["depress"] = depress
    _CACHE["checked"] = checked
    _CACHE["checked_disabled"] = checked_disabled
    _CACHE[key] = QPixmap()
    return empty, depress, checked, checked_disabled


class ThemeCheckBox(QAbstractButton):
    """Checkable control with WoW EmptySlot / Depress / CheckBox-Check art."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ThemeCheckBox")
        self.setText(text)
        self.setCheckable(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        # Kill any inherited QCheckBox/QAbstractButton QSS chrome for this instance.
        self.setStyleSheet(
            "QAbstractButton#ThemeCheckBox {"
            "  background: transparent; border: none; padding: 0; margin: 0;"
            "}"
        )
        self._hover = False
        self.setMinimumHeight(_INDICATOR_PX + 6)
        self.setContentsMargins(0, 0, 0, 0)

    def _indicator_rect(self) -> QRect:
        return QRect(
            0,
            max(0, (self.height() - _INDICATOR_PX) // 2),
            _INDICATOR_PX,
            _INDICATOR_PX,
        )

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        text = self.text()
        text_w = fm.horizontalAdvance(text) if text else 0
        w = _INDICATOR_PX + ((_LABEL_SPACING + text_w) if text else 0) + 4
        h = max(_INDICATOR_PX + 6, fm.height() + 8)
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        try:
            if not painter.isActive():
                return
            try:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                indicator = self._indicator_rect()
                self._paint_indicator(painter, indicator)

                text = self.text()
                if text:
                    label = QRect(
                        indicator.right() + 1 + _LABEL_SPACING,
                        0,
                        max(0, self.width() - indicator.right() - 1 - _LABEL_SPACING),
                        self.height(),
                    )
                    painter.setPen(QColor("#e6e0ee"))
                    flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    if self.isEnabled():
                        painter.drawText(label, flags, text)
                    else:
                        painter.setOpacity(0.45)
                        painter.drawText(label, flags, text)
                        painter.setOpacity(1.0)
            finally:
                painter.end()

        finally:
            if painter.isActive():
                painter.end()

    def _paint_indicator(self, painter: QPainter, rect: QRect) -> None:
        if not painter.isActive():
            return
        empty, depress, checked, checked_disabled = _assets()
        dest = QRect(rect)
        enabled = self.isEnabled()
        mark = checked_disabled if (not enabled and not checked_disabled.isNull()) else checked
        if not enabled:
            painter.setOpacity(0.45)

        if not empty.isNull():
            fx = dest.x() + (dest.width() - empty.width()) // 2
            fy = dest.y() + (dest.height() - empty.height()) // 2
            painter.drawPixmap(fx, fy, empty)

            if self.isChecked() and not mark.isNull():
                if not enabled:
                    painter.setOpacity(1.0)
                cx = fx + (empty.width() - mark.width()) // 2
                cy = fy + (empty.height() - mark.height()) // 2
                painter.drawPixmap(cx, cy, mark)
                if not enabled:
                    painter.setOpacity(0.45)

            if self._hover and enabled and not depress.isNull():
                painter.drawPixmap(fx, fy, depress)
        else:
            painter.setPen(QColor(150, 131, 158, 80))
            painter.setBrush(QColor(120, 100, 150, 30))
            painter.drawRoundedRect(dest.adjusted(1, 1, -1, -1), 4, 4)
            if self.isChecked():
                if not mark.isNull():
                    if not enabled:
                        painter.setOpacity(1.0)
                    cx = dest.x() + (dest.width() - mark.width()) // 2
                    cy = dest.y() + (dest.height() - mark.height()) // 2
                    painter.drawPixmap(cx, cy, mark)
                else:
                    painter.setPen(QColor(_GOLD))
                    # Minimal fallback tick if textures are missing.
                    mid = dest.center()
                    painter.drawLine(mid.x() - 5, mid.y(), mid.x() - 1, mid.y() + 4)
                    painter.drawLine(mid.x() - 1, mid.y() + 4, mid.x() + 6, mid.y() - 5)
        painter.setOpacity(1.0)
