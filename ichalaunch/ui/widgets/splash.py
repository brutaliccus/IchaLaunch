"""Lightweight startup splash — soft-edged *square* icon with a gentle breathe pulse."""

from __future__ import annotations

import math

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

# Warm dark pad behind the icon (RavenCraft-adjacent, not purple glow).
_PAD = QColor(18, 16, 14, 200)
_ICON_PX = 200
_WINDOW_PX = 280
# Fraction of the square side used as a soft border fade (not a radial circle).
_EDGE_FEATHER = 0.14
_MAX_LIFETIME_MS = 45_000


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def soft_edge_icon(source: QPixmap, size: int = _ICON_PX, feather: float = _EDGE_FEATHER) -> QPixmap:
    """Scale icon to a square and feather alpha along the *square* edges only.

    Uses distance-to-nearest-edge falloff so the icon stays square (not a circular crop).
    """
    if source.isNull():
        return QPixmap()

    scaled = source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)

    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    x = (size - scaled.width()) // 2
    y = (size - scaled.height()) // 2
    painter.drawPixmap(x, y, scaled)
    painter.end()

    feather_px = max(1.0, size * max(0.02, min(0.45, feather)))
    img = out.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    last = size - 1
    for py in range(size):
        for px in range(size):
            dist = min(px, py, last - px, last - py)
            if dist >= feather_px:
                continue
            factor = _smoothstep(dist / feather_px)
            c = img.pixelColor(px, py)
            if c.alpha() == 0:
                continue
            c.setAlpha(int(c.alpha() * factor))
            img.setPixelColor(px, py, c)
    return QPixmap.fromImage(img)


def soft_square_pad(size: int, color: QColor = _PAD, feather: float = _EDGE_FEATHER) -> QPixmap:
    """Warm dark square with the same edge feather (backdrop for the icon)."""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    feather_px = max(1.0, size * max(0.02, min(0.45, feather)))
    last = size - 1
    for py in range(size):
        for px in range(size):
            dist = min(px, py, last - px, last - py)
            if dist <= 0:
                a = 0
            elif dist >= feather_px:
                a = color.alpha()
            else:
                a = int(color.alpha() * _smoothstep(dist / feather_px))
            img.setPixelColor(px, py, QColor(color.red(), color.green(), color.blue(), a))
    return QPixmap.fromImage(img)


class SplashScreen(QWidget):
    """Frameless translucent splash shown while the main window initializes."""

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.SplashScreen
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(_WINDOW_PX, _WINDOW_PX)

        self._icon = soft_edge_icon(pixmap) if not pixmap.isNull() else QPixmap()
        self._pad = soft_square_pad(int(_ICON_PX * 1.12))
        self._breathe_t = 0.0
        self._scale = 1.0
        self._opacity = 0.88
        self._finished = False

        self._anim = QPropertyAnimation(self, b"breathe", self)
        self._anim.setDuration(1600)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.setLoopCount(-1)

        self._failsafe = QTimer(self)
        self._failsafe.setSingleShot(True)
        self._failsafe.timeout.connect(self.finish)

    def get_breathe(self) -> float:
        return self._breathe_t

    def set_breathe(self, t: float) -> None:
        self._breathe_t = t
        wave = 0.5 - 0.5 * math.cos(t * math.pi * 2)
        self._scale = 0.94 + 0.06 * wave
        self._opacity = 0.78 + 0.20 * wave
        self.update()

    breathe = Property(float, get_breathe, set_breathe)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._center_on_screen()
        if self._anim.state() != QPropertyAnimation.State.Running:
            self._anim.start()
        if not self._failsafe.isActive():
            self._failsafe.start(_MAX_LIFETIME_MS)

    def _center_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(
            geo.center().x() - self.width() // 2,
            geo.center().y() - self.height() // 2,
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(self._opacity)

        cx = self.width() * 0.5
        cy = self.height() * 0.5

        if not self._pad.isNull():
            pw = self._pad.width() * self._scale
            ph = self._pad.height() * self._scale
            painter.drawPixmap(
                QRectF(cx - pw * 0.5, cy - ph * 0.5, pw, ph),
                self._pad,
                QRectF(self._pad.rect()),
            )

        if self._icon.isNull():
            return

        iw = self._icon.width() * self._scale
        ih = self._icon.height() * self._scale
        target = QRectF(cx - iw * 0.5, cy - ih * 0.5, iw, ih)
        painter.drawPixmap(target, self._icon, QRectF(self._icon.rect()))

    def finish(self, widget: QWidget | None = None) -> None:
        """Stop breathe, optionally show *widget*, then close the splash."""
        if self._finished:
            return
        self._finished = True
        self._failsafe.stop()
        self._anim.stop()
        if widget is not None:
            widget.show()
            widget.raise_()
            widget.activateWindow()
        self.close()
        self.deleteLater()


def load_splash_pixmap() -> QPixmap:
    """Load app icon for splash (png preferred for alpha quality)."""
    from ichalaunch.core.paths import theme_file

    for name in ("ichalaunch.png", "ichalaunch.ico"):
        path = theme_file(name)
        if path.exists():
            pm = QPixmap(str(path))
            if not pm.isNull():
                return pm
    return QPixmap()
