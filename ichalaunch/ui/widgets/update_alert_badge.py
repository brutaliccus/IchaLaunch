"""WoW Adventure Guide alert badge for pending updates (nav + category tabs)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QPushButton, QWidget

from ichalaunch.core.paths import theme_file

TAB_ALERT_NAME = "AdventureGuideMicrobuttonAlert.PNG"
TAB_ALERT_EXTERNAL = Path(r"F:\wow-ui-textures\Buttons") / TAB_ALERT_NAME
TAB_ALERT_PX = 22

_cached_badge_pm: QPixmap | None = None


def _load_theme_texture(bundled_name: str, external: Path) -> QPixmap:
    bundled = theme_file(bundled_name)
    path = bundled if bundled.is_file() else external if external.is_file() else None
    if path is None:
        return QPixmap()
    pm = QPixmap(str(path))
    return pm if not pm.isNull() else QPixmap()


def update_alert_badge_pixmap() -> QPixmap:
    """Scaled Adventure Guide alert pixmap (cached)."""
    global _cached_badge_pm
    if _cached_badge_pm is not None:
        return _cached_badge_pm
    src = _load_theme_texture(TAB_ALERT_NAME, TAB_ALERT_EXTERNAL)
    if src.isNull():
        _cached_badge_pm = QPixmap()
        return _cached_badge_pm
    _cached_badge_pm = src.scaled(
        TAB_ALERT_PX,
        TAB_ALERT_PX,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return _cached_badge_pm


def paint_update_alert_badge(painter: QPainter, rect: QRect) -> None:
    """Paint the alert badge on the right of rect, vertically centered (fallback: gold dot)."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    alert = update_alert_badge_pixmap()
    if alert.isNull():
        radius = 4.5
        center_x = rect.right() - radius - 7.0
        center_y = rect.center().y()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#F1C22D"))
        painter.drawEllipse(center_x - radius, center_y - radius, radius * 2, radius * 2)
        return
    margin = 3
    x = rect.right() - alert.width() - margin + 1
    y = rect.top() + (rect.height() - alert.height()) // 2
    painter.drawPixmap(x, y, alert)


class BadgeNavButton(QPushButton):
    """Push button with optional pending-update alert badge (right, vertically centered)."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self._badge = False

    def set_badge_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._badge:
            return
        self._badge = visible
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._badge:
            return
        painter = QPainter(self)
        paint_update_alert_badge(painter, self.rect())
        painter.end()
