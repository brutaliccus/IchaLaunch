"""Classic WoW 9-slice tooltip chrome, sized to a short name."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from ichalaunch.core.paths import theme_file
from ichalaunch.ui.theme_fonts import ink_centered_rect

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
    # The chrome face, like Ready, Contributors and PLAY. The default UI face
    # made the one hover label in the bar the only thing not speaking it.
    from ichalaunch.ui.theme_fonts import chrome_family

    font.setFamily(chrome_family())
    font.setPixelSize(15)
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


def paint_wow_tooltip(  # noqa: PLR0913
    painter: QPainter,
    rect: QRect,
    text: str,
    font: QFont | None = None,
    pen=None,
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
    painter.setPen(pen if pen is not None else _TEXT)
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
        self._ramp = None
        self._tint = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def set_ramp(self, stops) -> None:
        """Colour stops for the animated text, sampled from the portrait art."""
        self._ramp = stops
        # The frame borrows the ramp's hottest colour, so border, glow and text
        # are three views of one palette rather than three unrelated choices.
        if stops:
            self._tint = QColor(stops[len(stops) // 2][1])

    def set_name(self, text: str) -> None:
        self._text = (text or "").strip()
        self._face = tooltip_font(self.font())
        # Sized for the ornate frame rather than the thin default one: the
        # border is real art with a crest on it, so the text needs clear space
        # inside or the ornament crowds the word.
        e = portrait_frame_edge()
        ref = QFont(self._face)
        ref.setPixelSize(_PF_REF_PX)
        rfm = QFontMetrics(ref)
        w = rfm.horizontalAdvance(self._text) + 2 * e + 2 * _PF_TEXT_PAD
        h = max(int(rfm.height() * 1.8) + 2 * e, e * 2 + 34)
        self.setFixedSize(int(w), int(h))
        self._face = self._fitted_face(int(w), int(h))

    def _fitted_face(self, w: int, h: int) -> QFont:
        """Grow the type until the ink nearly touches the frame's inner edge.

        The crest sits centre-top of the frame and hangs into the opening, so
        the usable height excludes it rather than only the border thickness.
        Without that, a tall ascender on a big size runs into the gold.
        """
        e = portrait_frame_edge()
        # Only a small breathing gap here, NOT the plate's padding again. That
        # padding is already inside the plate and subtracting it twice left the
        # type nothing to expand into, so every name came out at the reference
        # size no matter how much room its plate had.
        inner_w = max(8, w - 2 * e - 10)
        crest = _pf_load().get("crest")
        crest_h = crest.height() if crest is not None and not crest.isNull() else e
        inner_h = max(8, h - e - crest_h - 4)
        font = QFont(self._face)
        best = _PF_MIN_PX
        for px in range(_PF_MIN_PX, _PF_MAX_PX + 1):
            font.setPixelSize(px)
            fm = QFontMetrics(font)
            if fm.horizontalAdvance(self._text) > inner_w * _PF_FIT:
                break
            ink = max(fm.height(), fm.tightBoundingRect(self._text).height())
            if ink > inner_h:
                break
            best = px
        font.setPixelSize(best)
        return font

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
        from ichalaunch.ui.widgets.gradient_label import lava_ticker

        lava_ticker().subscribe(self)

    def dismiss(self) -> None:
        from ichalaunch.ui.widgets.gradient_label import lava_ticker

        lava_ticker().unsubscribe(self)
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        del event
        if not self._text:
            return
        painter = QPainter(self)
        if not painter.isActive():
            return
        from ichalaunch.ui.widgets.gradient_label import lava_text_pen, lava_ticker

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = self.rect()
        paint_portrait_frame(painter, rect, self._tint)
        pen = lava_text_pen(rect, lava_ticker().phase, self._ramp)
        painter.setFont(self._face)
        painter.setPen(pen)
        e = portrait_frame_edge()
        crest = _pf_load().get("crest")
        crest_h = crest.height() if crest is not None and not crest.isNull() else e
        # Ink-centred, not AlignCenter. Folkard's capitals sit low inside a tall
        # ascent, which is what put the R on the plate's top edge on the glue
        # buttons, and bigger type makes that offset worse rather than better.
        box = rect.adjusted(e, crest_h, -e, -e)
        box = ink_centered_rect(box, self._face, self._text)
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), self._text)
        painter.end()


# ---------------------------------------------------------------------------
# Portrait-frame tooltip. Built from the ornate border around an in-game
# Pandaren portrait, cut into a nine-slice with the middle punched out, so a box
# of any size can be framed in real vanilla art rather than in a rounded
# rectangle that speaks no particular language.
#
# Scaled to 0.62 for use. At native 26px the ornament is heavier than the words
# it surrounds: a four-letter name like "Icha" ends up mostly frame. 16px keeps
# the stonework and the gold corners readable while leaving the label the
# larger share of the plate.
# ---------------------------------------------------------------------------
# Generous, because this padding is the room the type GROWS INTO. Sized snugly
# the plate has nothing spare and every name settles at the reference size.
_PF_TEXT_PAD = 20
# Plate width comes from the name at this REFERENCE size, and the displayed type
# is then grown to fill the plate. Two passes, deliberately: if the plate sized
# itself from the grown type and the type grew to fill the plate, the two would
# chase each other. A longer name still earns a wider plate, and every name then
# maximises the plate it has, so the sizes differ per contributor by design.
_PF_REF_PX = 15
_PF_FIT = 0.88          # ink spans this much of the inner width
_PF_MAX_PX = 34         # a one-letter name must not blow the plate out
_PF_MIN_PX = 12
_PF_SCALE = 0.62
_PF_NAMES = {
    "tl": "portraitframe_tl.png", "tr": "portraitframe_tr.png",
    "bl": "portraitframe_bl.png", "br": "portraitframe_br.png",
    "t": "portraitframe_t.png", "b": "portraitframe_b.png",
    "l": "portraitframe_l.png", "r": "portraitframe_r.png",
    "crest": "portraitframe_crest.png",
}
_pf_tiles: dict | None = None
_PF_FILL = QColor(12, 10, 9, 249)


def _pf_load() -> dict:
    global _pf_tiles
    if _pf_tiles is not None:
        return _pf_tiles
    out: dict = {}
    for key, name in _PF_NAMES.items():
        path = _resolve_asset(name)
        pm = QPixmap(str(path)) if path is not None else QPixmap()
        if not pm.isNull() and _PF_SCALE != 1.0:
            pm = pm.scaled(
                max(1, round(pm.width() * _PF_SCALE)),
                max(1, round(pm.height() * _PF_SCALE)),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        out[key] = pm
    _pf_tiles = out
    return out


def portrait_frame_edge() -> int:
    tiles = _pf_load()
    tl = tiles.get("tl")
    return tl.width() if tl is not None and not tl.isNull() else 16


def paint_portrait_frame(painter: QPainter, rect: QRect, tint: QColor | None = None) -> None:
    """Nine-slice the portrait border around *rect*, interior filled dark.

    Corners are drawn at native size and only the edges stretch, which is the
    whole point: a stretched corner turns the gold ornament into a smear and
    reads as cheap. The crest is drawn ONCE, centred on the top edge, rather
    than being part of a tiled strip that would repeat it across the width.
    """
    tiles = _pf_load()
    e = portrait_frame_edge()
    if tiles.get("tl") is None or tiles["tl"].isNull():
        return
    painter.fillRect(rect.adjusted(e // 2, e // 2, -e // 2, -e // 2), _PF_FILL)

    inner_w = max(0, rect.width() - 2 * e)
    inner_h = max(0, rect.height() - 2 * e)
    if inner_w:
        painter.drawPixmap(QRect(rect.x() + e, rect.y(), inner_w, e), tiles["t"])
        painter.drawPixmap(
            QRect(rect.x() + e, rect.y() + rect.height() - e, inner_w, e), tiles["b"]
        )
    if inner_h:
        painter.drawPixmap(QRect(rect.x(), rect.y() + e, e, inner_h), tiles["l"])
        painter.drawPixmap(
            QRect(rect.x() + rect.width() - e, rect.y() + e, e, inner_h), tiles["r"]
        )
    painter.drawPixmap(rect.x(), rect.y(), tiles["tl"])
    painter.drawPixmap(rect.x() + rect.width() - e, rect.y(), tiles["tr"])
    painter.drawPixmap(rect.x(), rect.y() + rect.height() - e, tiles["bl"])
    painter.drawPixmap(
        rect.x() + rect.width() - e, rect.y() + rect.height() - e, tiles["br"]
    )
    crest = tiles.get("crest")
    if crest is not None and not crest.isNull() and rect.width() > crest.width() + 2 * e:
        painter.drawPixmap(
            rect.x() + (rect.width() - crest.width()) // 2, rect.y(), crest
        )
    if tint is not None:
        # A whisper of the contributor's own colour over the stone, so the frame,
        # the portrait glow and the text ramp read as one object. Kept low:
        # any more and it stops looking like lit metal and starts looking like
        # a coloured rectangle.
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Overlay)
        painter.setOpacity(0.30)
        painter.fillRect(rect, tint)
        painter.restore()
