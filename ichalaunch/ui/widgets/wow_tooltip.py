"""Classic WoW 9-slice tooltip chrome, sized to a short name."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from ichalaunch.core.paths import theme_file

_FALLBACK_DIR = Path(r"F:\wow-ui-textures\Tooltips")
_TILE_SCALE = 2
_PAD_X = 8
_PAD_Y = 3
_GAP_ABOVE = 2
_TEXT = QColor("#F1C22D")
_BG_NAME = "UI-Tooltip-Background-Corrupted.PNG"
_BG_OPACITY = 0.85
# Dark pixels baked into the 8x8 tiles are the old tooltip body — drop them.
_INNER_FILL_LUMA = 40
_INNER_FILL_ALPHA = 170

_PIECES = {
    "tl": "UI-Tooltip-TL.PNG",
    "t": "UI-Tooltip-T.PNG",
    "tr": "UI-Tooltip-TR.PNG",
    "l": "UI-Tooltip-L.PNG",
    "r": "UI-Tooltip-R.PNG",
    "bl": "UI-Tooltip-BL.PNG",
    "b": "UI-Tooltip-B.PNG",
    "br": "UI-Tooltip-BR.PNG",
}

_tiles: dict[str, QPixmap] | None = None
_bg: QPixmap | None = None


def _resolve_asset(name: str) -> Path | None:
    bundled = theme_file("tooltips", name)
    if bundled.is_file():
        return bundled
    fallback = _FALLBACK_DIR / name
    if fallback.is_file():
        return fallback
    return None


def _clear_inner_fill(pm: QPixmap) -> QPixmap:
    """Keep the silver rim / outer glow; punch out the baked-in dark body."""
    if pm.isNull():
        return pm
    img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if (
                max(c.red(), c.green(), c.blue()) < _INNER_FILL_LUMA
                and c.alpha() > _INNER_FILL_ALPHA
            ):
                c.setAlpha(0)
                img.setPixelColor(x, y, c)
    return QPixmap.fromImage(img)


def _load_tiles() -> dict[str, QPixmap]:
    global _tiles
    if _tiles is not None:
        return _tiles
    loaded: dict[str, QPixmap] = {}
    for key, name in _PIECES.items():
        path = _resolve_asset(name)
        pm = QPixmap(str(path)) if path is not None else QPixmap()
        if not pm.isNull():
            pm = _clear_inner_fill(pm)
            if _TILE_SCALE != 1:
                pm = pm.scaled(
                    pm.width() * _TILE_SCALE,
                    pm.height() * _TILE_SCALE,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
        loaded[key] = pm
    _tiles = loaded
    return loaded


def _load_background() -> QPixmap:
    global _bg
    if _bg is not None:
        return _bg
    path = _resolve_asset(_BG_NAME)
    pm = QPixmap(str(path)) if path is not None else QPixmap()
    _bg = pm
    return _bg


def tooltip_font(base: QFont | None = None) -> QFont:
    font = QFont(base) if base is not None else QFont()
    font.setFamily("Segoe UI")
    font.setPixelSize(12)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def tooltip_size_for(text: str, font: QFont | None = None) -> QSize:
    tiles = _load_tiles()
    edge = tiles["tl"].width() if not tiles["tl"].isNull() else 8 * _TILE_SCALE
    fm = QFontMetrics(font or tooltip_font())
    name = (text or "").strip() or " "
    inner_w = max(1, fm.horizontalAdvance(name) + _PAD_X * 2)
    inner_h = max(1, fm.height() + _PAD_Y * 2)
    return QSize(edge * 2 + inner_w, edge * 2 + inner_h)


def _tile_h(painter: QPainter, dest: QRect, tile: QPixmap) -> None:
    if dest.width() <= 0 or dest.height() <= 0 or tile.isNull():
        return
    painter.save()
    painter.setClipRect(dest)
    x = dest.x()
    while x < dest.x() + dest.width():
        painter.drawPixmap(x, dest.y(), tile)
        x += tile.width()
    painter.restore()


def _tile_rect(painter: QPainter, dest: QRect, tile: QPixmap) -> None:
    if dest.width() <= 0 or dest.height() <= 0 or tile.isNull():
        return
    painter.save()
    painter.setClipRect(dest)
    y = dest.y()
    while y < dest.y() + dest.height():
        x = dest.x()
        while x < dest.x() + dest.width():
            painter.drawPixmap(x, y, tile)
            x += tile.width()
        y += tile.height()
    painter.restore()


def _tile_v(painter: QPainter, dest: QRect, tile: QPixmap) -> None:
    if dest.width() <= 0 or dest.height() <= 0 or tile.isNull():
        return
    painter.save()
    painter.setClipRect(dest)
    y = dest.y()
    while y < dest.y() + dest.height():
        painter.drawPixmap(dest.x(), y, tile)
        y += tile.height()
    painter.restore()


def paint_wow_tooltip(
    painter: QPainter,
    rect: QRect,
    text: str,
    font: QFont | None = None,
) -> None:
    tiles = _load_tiles()
    tl, t, tr = tiles["tl"], tiles["t"], tiles["tr"]
    left, right = tiles["l"], tiles["r"]
    bl, b, br = tiles["bl"], tiles["b"], tiles["br"]
    edge = tl.width() if not tl.isNull() else 8 * _TILE_SCALE
    inner = rect.adjusted(edge, edge, -edge, -edge)
    bg = _load_background()
    if not bg.isNull():
        # Sit just inside the silver stroke (mid-tile), not only the inner hole.
        painter.save()
        painter.setOpacity(_BG_OPACITY)
        _tile_rect(painter, rect.adjusted(edge // 2, edge // 2, -(edge // 2), -(edge // 2)), bg)
        painter.restore()
    _tile_h(painter, QRect(rect.x() + edge, rect.y(), inner.width(), edge), t)
    _tile_h(
        painter,
        QRect(rect.x() + edge, rect.y() + rect.height() - edge, inner.width(), edge),
        b,
    )
    _tile_v(painter, QRect(rect.x(), rect.y() + edge, edge, inner.height()), left)
    _tile_v(
        painter,
        QRect(rect.x() + rect.width() - edge, rect.y() + edge, edge, inner.height()),
        right,
    )
    if not tl.isNull():
        painter.drawPixmap(rect.topLeft(), tl)
    if not tr.isNull():
        painter.drawPixmap(rect.x() + rect.width() - edge, rect.y(), tr)
    if not bl.isNull():
        painter.drawPixmap(rect.x(), rect.y() + rect.height() - edge, bl)
    if not br.isNull():
        painter.drawPixmap(
            rect.x() + rect.width() - edge,
            rect.y() + rect.height() - edge,
            br,
        )
    name = (text or "").strip()
    if not name:
        return
    face = font or tooltip_font()
    painter.setFont(face)
    painter.setPen(_TEXT)
    painter.drawText(inner, int(Qt.AlignmentFlag.AlignCenter), name)


def render_contributor_tooltip(text: str, font: QFont | None = None) -> QPixmap:
    face = font or tooltip_font()
    size = tooltip_size_for(text, face)
    pm = QPixmap(size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    paint_wow_tooltip(painter, QRect(QPoint(0, 0), size), text, face)
    painter.end()
    return pm


class ContributorNameTip(QWidget):
    """Frameless name plate, centered above a contributor portrait."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._text = ""
        self._face = tooltip_font()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def set_name(self, text: str) -> None:
        self._text = (text or "").strip()
        self._face = tooltip_font(self.font())
        self.setFixedSize(tooltip_size_for(self._text, self._face))

    def popup_above(self, anchor: QWidget) -> None:
        if not self._text or anchor is None:
            self.hide()
            return
        host = anchor.window()
        if host is None:
            return
        if self.parent() is not host:
            self.setParent(host)
        self.set_name(self._text)
        top_center = anchor.mapTo(host, QPoint(anchor.width() // 2, 0))
        x = top_center.x() - self.width() // 2
        y = top_center.y() - self.height() - _GAP_ABOVE
        x = max(4, min(x, max(4, host.width() - self.width() - 4)))
        y = max(4, y)
        self.move(x, y)
        self.show()
        self.raise_()

    def dismiss(self) -> None:
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        del event
        if not self._text:
            return
        painter = QPainter(self)
        if not painter.isActive():
            return
        paint_wow_tooltip(painter, self.rect(), self._text, self._face)
        painter.end()
