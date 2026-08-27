"""Lightweight startup splash — soft-edged icon over smoke, with a gentle breathe pulse."""

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

_ICON_PX = 450
# 2× previous smoke (~268 → 536); window grows so breathe scale never clips it.
_SMOKE_PX = 536
# Mid purple VFX layer — a bit smaller than the black Worgen smoke behind it.
_SMOKE_PURPLE_PX = 440
# Theme dark RavenCraft purple (stylesheet pressed / ApplyReadyButton:pressed).
_SMOKE_PURPLE = QColor(58, 36, 96)  # #3a2460
_WINDOW_PX = 560
# Fraction of the square side used as a soft border fade (not a radial circle).
_EDGE_FEATHER = 0.14
_MAX_LIFETIME_MS = 45_000


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def soft_edge_icon(source: QPixmap, size: int = _ICON_PX, feather: float = _EDGE_FEATHER) -> QPixmap:
    """Scale icon to a square and feather alpha along the *square* edges only.

    Uses distance-to-nearest-edge falloff so the icon stays square (not a circular crop).
    Source should already have a transparent backdrop (see ravencraft.png).
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


def _scale_theme_pixmap(name: str, size: int) -> QPixmap:
    """Load a theme PNG and scale it to *size* (KeepAspectRatio)."""
    from ichalaunch.core.paths import theme_file

    path = theme_file(name)
    if not path.exists():
        return QPixmap()
    source = QPixmap(str(path))
    if source.isNull():
        return QPixmap()
    return source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def load_smoke_pixmap(size: int = _SMOKE_PX) -> QPixmap:
    """Load bundled Worgen smoke texture, scaled larger than the splash icon."""
    return _scale_theme_pixmap("Worgen_Smoke_01.PNG", size)


def _tint_smoke_purple(src: QPixmap, tint: QColor = _SMOKE_PURPLE) -> QPixmap:
    """Pixel-tint smoke to dark purple; wisps at half opacity (no solid fill/box).

    RGB = luminance(source) × purple. Alpha is source alpha × 0.5 so wisps stay
    half-opaque. Transparent pixels stay fully clear — never a rectangular plate.
    """
    if src.isNull():
        return QPixmap()
    img = src.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    tr, tg, tb = tint.red(), tint.green(), tint.blue()
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            a = c.alpha()
            if a == 0:
                img.setPixelColor(x, y, QColor(0, 0, 0, 0))
                continue
            lum = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0
            img.setPixelColor(
                x,
                y,
                QColor(int(tr * lum), int(tg * lum), int(tb * lum), int(a * 0.5)),
            )
    return QPixmap.fromImage(img)


def load_purple_smoke_pixmap(size: int = _SMOKE_PURPLE_PX) -> QPixmap:
    """Load T_VFX_Smoke_C, scaled smaller than black smoke, pixel-tinted dark purple."""
    return _tint_smoke_purple(_scale_theme_pixmap("T_VFX_Smoke_C.PNG", size))


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
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.setFixedSize(_WINDOW_PX, _WINDOW_PX)

        self._smoke = load_smoke_pixmap()
        self._smoke_purple = load_purple_smoke_pixmap()
        self._icon = soft_edge_icon(pixmap) if not pixmap.isNull() else QPixmap()
        self._breathe_t = 0.0
        self._scale = 1.0
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
        # Scale-only pulse; splash art stays at full opacity.
        self._scale = 0.94 + 0.06 * wave
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
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setOpacity(1.0)

        cx = self.width() * 0.5
        cy = self.height() * 0.5

        # Layer: large black smoke → smaller dark-purple smoke → icon (front).
        # No dark fill — widget backdrop stays translucent.
        if not self._smoke.isNull():
            sw = self._smoke.width() * self._scale
            sh = self._smoke.height() * self._scale
            painter.drawPixmap(
                QRectF(cx - sw * 0.5, cy - sh * 0.5, sw, sh),
                self._smoke,
                QRectF(self._smoke.rect()),
            )

        if not self._smoke_purple.isNull():
            pw = self._smoke_purple.width() * self._scale
            ph = self._smoke_purple.height() * self._scale
            painter.drawPixmap(
                QRectF(cx - pw * 0.5, cy - ph * 0.5, pw, ph),
                self._smoke_purple,
                QRectF(self._smoke_purple.rect()),
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
    """Load splash art (RavenCraft logo; not the Addons-tab ichalaunch icon)."""
    from ichalaunch.core.paths import theme_file

    # Home brand crest — already transparent. Do not fall back to
    # ichalaunch.png / ichalaunch.ico (those stay the Addons-tab / window icon).
    path = theme_file("ravencraft.png")
    if path.exists():
        pm = QPixmap(str(path))
        if not pm.isNull():
            return pm
    return QPixmap()
