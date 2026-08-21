"""WoW Glue-Panel buttons (Up/Down art) for main toolbar actions."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QPushButton, QSizePolicy

from ichalaunch.core.paths import theme_file
from ichalaunch.ui.widgets.cursors import apply_open_hand

_UP_NAME = "Glue-Panel-Button-Up-v2.PNG"
_DOWN_NAME = "Glue-Panel-Button-Down-v2.PNG"
_UP_EXTERNAL = Path(r"F:\wow-ui-textures\GLUES\COMMON\Glue-Panel-Button-Up-v2.PNG")
_DOWN_EXTERNAL = Path(r"F:\wow-ui-textures\GLUES\COMMON\Glue-Panel-Button-Down-v2.PNG")

# Shared toolbar size so Rescan / Check Updates / Update All / + Git Repo match.
GLUE_BTN_W = 128
GLUE_BTN_H = 34
# Compact size for Addons / Client row actions (Install, Update, Remove, …).
GLUE_ROW_W = 100
GLUE_ROW_H = 28
GLUE_ROW_MENU_W = 28

_GOLD = QColor("#F1C22D")
_GOLD_SOFT = QColor("#E8C878")
_TEXT = QColor("#e6e0ee")
_TEXT_DIM = QColor("#8a8490")

# HSV targets for the red *fill* only (border greys are left alone).
# Standard = muted greyish purple; primary = RavenCraft bright purple.
_FILL_STANDARD = (275, 95, 1.05)   # hue, sat, value scale
_FILL_PRIMARY = (268, 175, 1.25)
_FILL_PRIMARY_BRIGHT = (265, 200, 1.35)

_RAW: dict[str, QImage] = {}
_RECOLOR: dict[tuple[str, str, bool], QPixmap] = {}


def _load_image(bundled: str, external: Path) -> QImage:
    cached = _RAW.get(bundled)
    if cached is not None:
        return cached
    path = theme_file(bundled)
    if not path.is_file():
        path = external
    img = QImage(str(path)) if path.is_file() else QImage()
    if not img.isNull() and img.format() != QImage.Format.Format_ARGB32:
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
    _RAW[bundled] = img
    return img


def _is_red_fill(c: QColor) -> bool:
    """True for the terracotta/red panel fill — not the metal border."""
    if c.alpha() < 16:
        return False
    r, g, b = c.red(), c.green(), c.blue()
    # Pure / near-pure reds dominate the fill (g and b near 0).
    if r >= 28 and r > g + 18 and r > b + 18 and (g + b) < r * 0.55:
        return True
    h = c.hue()
    if h < 0:
        return False
    return (h <= 18 or h >= 345) and c.saturation() >= 70 and c.value() >= 25


def _recolor_fill(src: QImage, hue: int, sat: int, value_scale: float) -> QPixmap:
    if src.isNull():
        return QPixmap()
    out = src.copy()
    for y in range(out.height()):
        for x in range(out.width()):
            c = QColor.fromRgba(out.pixel(x, y))
            if not _is_red_fill(c):
                continue
            v = max(0, min(255, int(round(c.value() * value_scale))))
            nc = QColor.fromHsv(hue, sat, v, c.alpha())
            out.setPixel(x, y, nc.rgba())
    return QPixmap.fromImage(out)


def _colored_pm(bundled: str, external: Path, role: str, disabled: bool) -> QPixmap:
    key = (bundled, role, disabled)
    hit = _RECOLOR.get(key)
    if hit is not None:
        return hit
    src = _load_image(bundled, external)
    if role == "primary_bright":
        hue, sat, scale = _FILL_PRIMARY_BRIGHT
    elif role == "primary":
        hue, sat, scale = _FILL_PRIMARY
    else:
        hue, sat, scale = _FILL_STANDARD
    pm = _recolor_fill(src, hue, sat, scale)
    if disabled and not pm.isNull():
        dim = QPixmap(pm.size())
        dim.fill(Qt.GlobalColor.transparent)
        p = QPainter(dim)
        p.drawPixmap(0, 0, pm)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        p.fillRect(dim.rect(), QColor(30, 28, 34, 100))
        p.end()
        pm = dim
    _RECOLOR[key] = pm
    return pm


def glue_chrome_pixmap(
    *,
    pressed: bool = False,
    role: str = "standard",
    disabled: bool = False,
) -> QPixmap:
    """Recolored Glue-Panel Up/Down art (red fill → purple; borders unchanged)."""
    if role not in ("standard", "primary", "primary_bright"):
        role = "standard"
    name, ext = (_DOWN_NAME, _DOWN_EXTERNAL) if pressed else (_UP_NAME, _UP_EXTERNAL)
    return _colored_pm(name, ext, role, disabled)


class GluePanelButton(QPushButton):
    """Main toolbar button painted with Glue-Panel Up/Down PNGs.

    Recolors only the red fill to purple; metal borders stay original.
    Not used for PLAY / REGISTER HERE (LaunchButton) or inline row actions.
    """

    def __init__(
        self,
        text: str = "",
        parent=None,
        *,
        role: str = "standard",
        width: int = GLUE_BTN_W,
        height: int = GLUE_BTN_H,
    ):
        super().__init__(text, parent)
        assert role in ("standard", "primary")
        self._role = role
        self._pulse = False
        self.setObjectName("GluePanelButton")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(int(width), int(height))
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setStyleSheet(
            "QPushButton#GluePanelButton {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
        )
        # Warm caches so first paint is snappy.
        for name, ext in ((_UP_NAME, _UP_EXTERNAL), (_DOWN_NAME, _DOWN_EXTERNAL)):
            _colored_pm(name, ext, "standard", False)
            _colored_pm(name, ext, "primary", False)

    def set_role(self, role: str) -> None:
        if role not in ("standard", "primary"):
            return
        if role != self._role:
            self._role = role
            self.update()

    def set_pulse(self, on: bool) -> None:
        on = bool(on)
        if on != self._pulse:
            self._pulse = on
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self.update()

    def _role_key(self) -> str:
        if self._role == "primary" and (self._pulse or self.underMouse()):
            return "primary_bright"
        return self._role

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        rect = self.rect()
        name, ext = (_DOWN_NAME, _DOWN_EXTERNAL) if self.isDown() else (_UP_NAME, _UP_EXTERNAL)
        pm = _colored_pm(name, ext, self._role_key(), not self.isEnabled())
        if pm.isNull():
            self._paint_fallback(painter, rect)
        else:
            painter.drawPixmap(rect, pm)

        if self.isEnabled() and (self.underMouse() or self._pulse) and self._role == "primary":
            pen = QPen(_GOLD if self._pulse else _GOLD_SOFT)
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 4, 4)

        self._paint_label(painter, rect)
        painter.end()

    def _paint_fallback(self, painter: QPainter, rect: QRect) -> None:
        fill = QColor("#4a2f7a") if self._role == "primary" else QColor("#2c2632")
        painter.setPen(QColor("#7a6e88"))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)

    def _paint_label(self, painter: QPainter, rect: QRect) -> None:
        text = self.text() or ""
        font = QFont(self.font())
        font.setFamily("Segoe UI")
        font.setBold(True)
        n = len(text)
        if n >= 14:
            px = 11
        elif n >= 11:
            px = 12
        else:
            px = 13
        font.setPixelSize(px)
        painter.setFont(font)

        # Same label color for standard/primary — only the fill is recolored.
        color = _TEXT_DIM if not self.isEnabled() else _TEXT
        text_rect = rect.adjusted(0, 1 if self.isDown() else 0, 0, 0)
        painter.setPen(QColor(0, 0, 0, 140))
        painter.drawText(text_rect.adjusted(1, 1, 1, 1), Qt.AlignmentFlag.AlignCenter, text)
        painter.setPen(color)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)
