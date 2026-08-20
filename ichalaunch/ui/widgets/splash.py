"""Lightweight startup splash — soft-edged icon with a gentle breathe pulse."""

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
    QBrush,
    QColor,
    QGuiApplication,
    QPainter,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

# Warm dark pad behind the icon (RavenCraft-adjacent, not purple glow).
_PAD = QColor(18, 16, 14, 210)
_ICON_PX = 200
_WINDOW_PX = 280
_FEATHER = 0.38
_MAX_LIFETIME_MS = 45_000


def soft_edge_icon(source: QPixmap, size: int = _ICON_PX, feather: float = _FEATHER) -> QPixmap:
    """Scale icon and feather alpha toward the edges (no hard square)."""
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

    radius = size * 0.5
    solid = max(0.05, 1.0 - feather)
    grad = QRadialGradient(size * 0.5, size * 0.5, radius)
    grad.setColorAt(0.0, QColor(0, 0, 0, 255))
    grad.setColorAt(solid, QColor(0, 0, 0, 255))
    grad.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.fillRect(out.rect(), QBrush(grad))
    painter.end()
    return out


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
        pad_r = min(self.width(), self.height()) * 0.48
        pad_grad = QRadialGradient(cx, cy, pad_r)
        pad_grad.setColorAt(0.0, _PAD)
        pad_grad.setColorAt(0.55, QColor(_PAD.red(), _PAD.green(), _PAD.blue(), 120))
        pad_grad.setColorAt(1.0, QColor(_PAD.red(), _PAD.green(), _PAD.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(pad_grad))
        painter.drawEllipse(QRectF(cx - pad_r, cy - pad_r, pad_r * 2, pad_r * 2))

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
