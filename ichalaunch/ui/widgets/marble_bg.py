"""Opaque dark underlay + tiled UI-Background-Marble at 50% opacity.

Paint rules (avoid ``engine == 0`` spam under translucent MainWindow):
- One QPainter per paintEvent / viewport Paint filter — never nest after style paint
  in a way that re-begins on the same device during hover cascades.
- Never use WA_TranslucentBackground on scroll viewports.
- QListWidget / QScrollArea marble is drawn on the *viewport* during its Paint
  event (item hover only repaints the viewport — not a second frame painter).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QRegion,
    QResizeEvent,
)
from PySide6.QtWidgets import QFrame, QListWidget, QScrollArea, QWidget

from ichalaunch.core.paths import theme_file
from ichalaunch.ui.widgets.common import Card

_BUNDLED_NAME = "UI-Background-Marble.PNG"
_EXTERNAL = Path(r"F:\wow-ui-textures\FrameGeneral\UI-Background-Marble.PNG")

_BASE = QColor("#181315")
_TILE_OPACITY = 0.50
_BORDER = QColor(124, 92, 196, 76)
_BORDER_GREY = QColor(150, 131, 158, 46)
_BORDER_INSET = 1


def resolve_marble_path() -> Path | None:
    bundled = theme_file(_BUNDLED_NAME)
    if bundled.is_file():
        return bundled
    if _EXTERNAL.is_file():
        return _EXTERNAL
    return None


def load_marble_pixmap() -> QPixmap:
    path = resolve_marble_path()
    if path is None:
        return QPixmap()
    pm = QPixmap(str(path))
    return pm if not pm.isNull() else QPixmap()


def paint_marble_tiled(
    painter: QPainter,
    rect: QRect,
    tile: QPixmap,
    *,
    radius: float = 0.0,
) -> None:
    """Fill opaque dark base, then tile marble at 50% opacity."""
    if not painter.isActive():
        return
    if rect.width() <= 0 or rect.height() <= 0:
        return

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    if radius > 0:
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), radius, radius)
        painter.setClipPath(path)
        painter.fillPath(path, _BASE)
    else:
        painter.fillRect(rect, _BASE)

    if not tile.isNull():
        tw, th = tile.width(), tile.height()
        if tw > 0 and th > 0:
            painter.setOpacity(_TILE_OPACITY)
            y = rect.top()
            bottom = rect.top() + rect.height()
            right = rect.left() + rect.width()
            while y < bottom:
                x = rect.left()
                while x < right:
                    painter.drawPixmap(x, y, tile)
                    x += tw
                y += th
            painter.setOpacity(1.0)

    painter.restore()


def _draw_round_stroke(painter: QPainter, rect: QRect, radius: float, color: QColor) -> None:
    if not painter.isActive() or rect.width() <= 0 or rect.height() <= 0:
        return
    painter.save()
    pen = QPen(color, 1.0)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # Inset half-pixel so the 1px stroke sits on the widget edge.
    r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    painter.drawRoundedRect(r, radius, radius)
    painter.restore()


def _prep_transparent_viewport(vp: QWidget) -> None:
    vp.setAutoFillBackground(False)
    vp.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    vp.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
    vp.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
    vp.setStyleSheet("background: transparent; border: none;")
    pal = vp.palette()
    pal.setColor(vp.backgroundRole(), QColor(0, 0, 0, 0))
    vp.setPalette(pal)


class MarblePanel(QWidget):
    """Client nav / Addons outer window — single-painter marble + stroke."""

    def __init__(self, parent: QWidget | None = None, *, radius: float = 10.0) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self._tile = load_marble_pixmap()
        self._radius = radius

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        # Clip children (nav highlights) to the rounded purple frame.
        # Never apply an empty mask — on a translucent MainWindow that punches
        # a desktop hole through the whole panel.
        r = self.rect()
        if r.width() <= 0 or r.height() <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), self._radius, self._radius)
        poly = path.toFillPolygon().toPolygon()
        if poly.isEmpty():
            return
        self.setMask(QRegion(poly))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        if not painter.isActive():
            return
        try:
            paint_marble_tiled(painter, self.rect(), self._tile, radius=self._radius)
            _draw_round_stroke(painter, self.rect(), self._radius, _BORDER)
        finally:
            painter.end()


class MarbleCard(Card):
    """Settings section card — single-painter marble + purple stroke."""

    def __init__(self, parent: QWidget | None = None, *, radius: float = 10.0) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        # Card QSS fill would fight custom paint; clear instance chrome.
        self.setStyleSheet(
            "QFrame#Card { background: transparent; border: none; }"
        )
        self._tile = load_marble_pixmap()
        self._radius = radius

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        if not painter.isActive():
            return
        try:
            paint_marble_tiled(painter, self.rect(), self._tile, radius=self._radius)
            _draw_round_stroke(painter, self.rect(), self._radius, _BORDER)
        finally:
            painter.end()


class MarbleListWidget(QListWidget):
    """Addon lists — marble painted on the viewport during its Paint event only."""

    def __init__(self, parent: QWidget | None = None, *, radius: float = 8.0) -> None:
        super().__init__(parent)
        self.setObjectName("MarbleList")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self._tile = load_marble_pixmap()
        self._radius = radius
        vp = self.viewport()
        _prep_transparent_viewport(vp)
        vp.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.viewport() and event.type() == QEvent.Type.Paint:
            # Draw marble under rows. Must not create a second painter on the
            # QListWidget frame — item:hover only repaints the viewport.
            painter = QPainter(obj)
            if painter.isActive():
                try:
                    # Viewport has no border; fill edge-to-edge (radius matches list).
                    paint_marble_tiled(
                        painter,
                        obj.rect(),  # type: ignore[arg-type]
                        self._tile,
                        radius=max(0.0, self._radius - float(_BORDER_INSET)),
                    )
                finally:
                    painter.end()
            return False  # continue so items / item widgets paint
        return super().eventFilter(obj, event)


class MarbleScrollArea(QScrollArea):
    """Client tweaks list — fixed marble on viewport Paint (hover-safe)."""

    def __init__(self, parent: QWidget | None = None, *, radius: float = 10.0) -> None:
        super().__init__(parent)
        self.setObjectName("ClientCatScroll")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._tile = load_marble_pixmap()
        self._radius = radius
        vp = self.viewport()
        _prep_transparent_viewport(vp)
        vp.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.viewport() and event.type() == QEvent.Type.Paint:
            painter = QPainter(obj)
            if painter.isActive():
                try:
                    paint_marble_tiled(
                        painter,
                        obj.rect(),  # type: ignore[arg-type]
                        self._tile,
                        radius=max(0.0, self._radius - float(_BORDER_INSET)),
                    )
                finally:
                    painter.end()
            return False
        return super().eventFilter(obj, event)

    def setWidget(self, widget: QWidget | None) -> None:  # noqa: N802
        super().setWidget(widget)
        if widget is not None:
            widget.setAutoFillBackground(False)
            widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
