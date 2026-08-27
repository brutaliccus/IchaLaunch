"""RavenCraft-themed radio with WoW UI-RADIOBUTTON atlas art.

Uses QAbstractButton (not QRadioButton) so QStyleSheetStyle never paints
QRadioButton::indicator:hover — same rationale as ThemeCheckBox.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import QAbstractButton, QWidget

from ichalaunch.core.paths import theme_file

_FALLBACK_DIR = Path(r"F:\wow-ui-textures\Buttons")
_ATLAS = "UI-RADIOBUTTON.PNG"

_OFF = "UI-RadioButton-Off.PNG"
_ON = "UI-RadioButton-On.PNG"
_HOVER = "UI-RadioButton-Hover.PNG"
_DISABLED = "UI-RadioButton-Disabled.PNG"

_INDICATOR_PX = 22
_LABEL_SPACING = 10
# Cropped gold/grey inner orb, sized to fill the Off ring's hole.
_DOT_FIT_SCALE = 0.64
# RGB sum at or below this is empty (BLP black well / sphere recesses).
_BLACK_SUM = 64

_GOLD = QColor("#F1C22D")

_CACHE: dict[str, QPixmap] = {}


def _resolve_asset(name: str) -> Path | None:
    bundled = theme_file("radios", name)
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


def _slice_atlas(index: int) -> QPixmap:
    path = _FALLBACK_DIR / _ATLAS
    if not path.is_file():
        return QPixmap()
    pm = QPixmap(str(path))
    if pm.isNull():
        return QPixmap()
    h = pm.height()
    if h <= 0:
        return QPixmap()
    x0 = index * h
    if x0 + h > pm.width():
        return QPixmap()
    return pm.copy(x0, 0, h, h)


def _knockout_near_black(pm: QPixmap) -> QPixmap:
    """Treat BLP-style black wells as transparent so gold does not muddy to grey."""
    if pm.isNull():
        return QPixmap()
    img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.alpha() == 0:
                continue
            if c.red() + c.green() + c.blue() <= _BLACK_SUM:
                c.setAlpha(0)
                img.setPixelColor(x, y, c)
    return QPixmap.fromImage(img)


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


def _scaled_indicator(pm: QPixmap) -> QPixmap:
    if pm.isNull():
        return QPixmap()
    return pm.scaled(
        _INDICATOR_PX,
        _INDICATOR_PX,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _prepare_ring(pm: QPixmap) -> QPixmap:
    if pm.isNull():
        return QPixmap()
    return _scaled_indicator(_knockout_near_black(pm))


def _prepare_dot(pm: QPixmap) -> QPixmap:
    """Crop the inner orb and scale it to sit inside the Off ring."""
    if pm.isNull():
        return QPixmap()
    cleaned = _knockout_near_black(pm)
    bounds = _opaque_bounds(cleaned)
    cropped = cleaned.copy(bounds) if bounds.isValid() else cleaned
    side = max(1, int(round(_INDICATOR_PX * _DOT_FIT_SCALE)))
    return cropped.scaled(
        side,
        side,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _assets() -> tuple[QPixmap, QPixmap, QPixmap, QPixmap]:
    key = "radio_gold_dot_v2"
    if key in _CACHE:
        return (
            _CACHE["off"],
            _CACHE["hover"],
            _CACHE["on"],
            _CACHE["on_disabled"],
        )

    raw_off = _load_raw(_OFF)
    raw_hover = _load_raw(_HOVER)
    raw_on = _load_raw(_ON)
    raw_disabled = _load_raw(_DISABLED)

    if raw_off.isNull():
        raw_off = _slice_atlas(0)
    if raw_on.isNull():
        raw_on = _slice_atlas(1)
    if raw_hover.isNull():
        raw_hover = _slice_atlas(2)
    if raw_disabled.isNull():
        raw_disabled = _slice_atlas(3)

    off = _prepare_ring(raw_off)
    hover = _scaled_indicator(raw_hover)
    on = _prepare_dot(raw_on)
    on_disabled = _prepare_dot(raw_disabled)

    _CACHE["off"] = off
    _CACHE["hover"] = hover
    _CACHE["on"] = on
    _CACHE["on_disabled"] = on_disabled
    _CACHE[key] = QPixmap()
    return off, hover, on, on_disabled


class ThemeRadioButton(QAbstractButton):
    """Checkable control with WoW UI-RadioButton Off / On / Hover art."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ThemeRadioButton")
        self.setText(text)
        self.setCheckable(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setStyleSheet(
            "QAbstractButton#ThemeRadioButton {"
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

    def _paint_indicator(self, painter: QPainter, rect: QRect) -> None:
        if not painter.isActive():
            return
        off, hover, on, on_disabled = _assets()
        dest = QRect(rect)
        enabled = self.isEnabled()
        # Slice 1 (gold) when enabled; slice 3 (muted) only when disabled+checked.
        if enabled:
            mark = on
        else:
            mark = on_disabled if not on_disabled.isNull() else on
        if not enabled:
            painter.setOpacity(0.45)

        def _draw_centered(pm: QPixmap) -> None:
            if pm.isNull():
                return
            cx = dest.x() + (dest.width() - pm.width()) // 2
            cy = dest.y() + (dest.height() - pm.height()) // 2
            painter.drawPixmap(cx, cy, pm)

        if not off.isNull():
            _draw_centered(off)

            if self.isChecked() and not mark.isNull():
                if not enabled:
                    painter.setOpacity(1.0)
                _draw_centered(mark)
                if not enabled:
                    painter.setOpacity(0.45)

            if self._hover and enabled and not hover.isNull() and not self.isChecked():
                _draw_centered(hover)
        else:
            painter.setPen(QColor(150, 131, 158, 80))
            painter.setBrush(QColor(120, 100, 150, 30))
            painter.drawEllipse(dest.adjusted(2, 2, -2, -2))
            if self.isChecked():
                if not mark.isNull():
                    if not enabled:
                        painter.setOpacity(1.0)
                    _draw_centered(mark)
                else:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(_GOLD)
                    painter.drawEllipse(dest.adjusted(6, 6, -6, -6))
        painter.setOpacity(1.0)
