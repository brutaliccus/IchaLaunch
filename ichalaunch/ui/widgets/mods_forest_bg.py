"""Soft Dark Forest texture behind the HOME installed-mods card."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from ichalaunch.core.paths import theme_file
from ichalaunch.ui.widgets.common import Card

_BUNDLED_NAME = "darkforest_tree_011.png"
_EXTERNAL = Path(r"F:\wow-ui-textures\GLUES\Models\UI_SCOURGE\DarkForest_Tree_011.PNG")

# Soft enough that gold category labels / list text stay readable.
_CARD_BASE = QColor("#181315")
_ART_OPACITY = 0.42
_WASH = QColor(24, 19, 21, 168)
_RADIUS = 10.0
_EDGE_FEATHER = 0.10


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def resolve_mods_forest_path() -> Path | None:
    bundled = theme_file(_BUNDLED_NAME)
    if bundled.is_file():
        return bundled
    if _EXTERNAL.is_file():
        return _EXTERNAL
    return None


def load_mods_forest_pixmap() -> QPixmap:
    path = resolve_mods_forest_path()
    if path is None:
        return QPixmap()
    pm = QPixmap(str(path))
    return pm if not pm.isNull() else QPixmap()


def _edge_mask(width: int, height: int, feather: float = _EDGE_FEATHER) -> QPixmap:
    """Soft alpha falloff on all edges so the art doesn't fight the card border."""
    if width <= 0 or height <= 0:
        return QPixmap()
    base_w, base_h = 128, 128
    min_base = float(min(base_w, base_h))
    feather_px = max(1.0, min_base * max(0.02, min(0.45, feather)))
    img = QImage(base_w, base_h, QImage.Format.Format_ARGB32)
    last_x = base_w - 1
    last_y = base_h - 1
    for y in range(base_h):
        for x in range(base_w):
            dist = min(x, last_x - x, y, last_y - y)
            if dist <= 0:
                a = 0.0
            elif dist < feather_px:
                a = _smoothstep(dist / feather_px)
            else:
                a = 1.0
            img.setPixelColor(x, y, QColor(255, 255, 255, int(255 * a)))
    small = QPixmap.fromImage(img)
    if width == base_w and height == base_h:
        return small
    return small.scaled(
        width,
        height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


class HomeModsCard(Card):
    """Installed-mods Card with a dimmed, feathered Dark Forest panel background."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Own chrome via #HomeModsCard — avoid QFrame.Card opaque fill covering art.
        self.setObjectName("HomeModsCard")
        self.setProperty("class", "")
        self._src = load_mods_forest_pixmap()
        self._mask = QPixmap()
        self._mask_w = 0
        self._mask_h = 0

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), _RADIUS, _RADIUS)
        painter.setClipPath(path)
        # Opaque card base so BloodElf floor never shows through.
        painter.fillPath(path, _CARD_BASE)

        if not self._src.isNull() and self.width() > 0 and self.height() > 0:
            scaled = self._src.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (rect.width() - scaled.width()) // 2
            y = (rect.height() - scaled.height()) // 2

            layer = QPixmap(rect.size())
            layer.fill(Qt.GlobalColor.transparent)
            lp = QPainter(layer)
            lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            lp.drawPixmap(x, y, scaled)

            if self._mask_w != rect.width() or self._mask_h != rect.height() or self._mask.isNull():
                self._mask = _edge_mask(rect.width(), rect.height())
                self._mask_w = rect.width()
                self._mask_h = rect.height()
            if not self._mask.isNull():
                lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                lp.drawPixmap(0, 0, self._mask)
            lp.end()

            painter.setOpacity(_ART_OPACITY)
            painter.drawPixmap(0, 0, layer)
            painter.setOpacity(1.0)
            painter.fillPath(path, _WASH)

        painter.end()
        super().paintEvent(event)
