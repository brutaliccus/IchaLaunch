"""Taller WoW glue-panel launch buttons (PLAY / REGISTER / UPDATE)."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import QPushButton, QSizePolicy

from ichalaunch.core.paths import theme_file
from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.widgets.glue_panel_button import launch_glue_chrome
from ichalaunch.ui.theme_fonts import chrome_family, ink_centered_rect

# RavenCraft palette
_GOLD = QColor("#F1C22D")
_GOLD_SOFT = QColor("#E8C878")
_MUTED = QColor("#6a6358")
# The Glue plate art is red in the original WoW textures. It is recoloured to
# the site's bronze rather than the launcher's old purple: purple appears nowhere
# on ravencraft.io and read as the most off-brand element in the window.
_PLATE_TINT = QColor("#c9953f")

_PLAY_W = 200
_PLAY_H = 56
# Label box inside the plate. The chrome bevel eats about 8px a side, so the
# ink is kept clear of it rather than run to the pixel edge.
_LABEL_H_PAD = 18
_LABEL_V_PAD = 16
# A 56px plate; past this the ink crowds the bevel however well it fits.
_LABEL_MAX_PX = 34
# Optical drop. Ink centring puts the marks on the rect centre, but the plate's
# top bevel reads thicker than its bottom, so true centre still sits high. A
# taste value, not a derived one.
_LABEL_NUDGE_Y = 2
_UPDATE_SIDE = 56
# CheckButtonGlow is 64×64: 9px empty pad, 46px halo, 32px inner hole,
# bright gold line just outside the hole. Crop empty pad only, then scale
# so the hole matches the 56px plate — the ring sits ~2px outside it.
_GLOW_NAME = "CheckButtonGlow.PNG"
_GLOW_EXTERNAL = Path(r"F:\wow-ui-textures\Buttons") / _GLOW_NAME
_GLOW_PAD_ALPHA = 8
_GLOW_CACHE: QPixmap | None = None
_STREAM_ARROW = "UI-MicroStream-Yellow.PNG"
_STREAM_ARROW_FALLBACK = Path(r"F:\wow-ui-textures\Buttons") / _STREAM_ARROW
_STREAM_ARROW_PX = 28
_STREAM_ARROW_CACHE: QPixmap | None = None


def _alpha_bounds(img: QImage, min_alpha: int) -> QRect:
    w, h = img.width(), img.height()
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if img.pixelColor(x, y).alpha() >= min_alpha:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if max_x < min_x:
        return QRect()
    return QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def _inner_hole_width(img: QImage) -> int:
    """Width of the transparent opening on the mid row (between the gold lines)."""
    cy = img.height() // 2
    w = img.width()
    left_ring = None
    for x in range(w):
        if img.pixelColor(x, cy).alpha() >= 64:
            left_ring = x
            break
    if left_ring is None:
        return 0
    hole_start = None
    for x in range(left_ring + 1, w):
        if img.pixelColor(x, cy).alpha() < 8:
            hole_start = x
            break
    if hole_start is None:
        return 0
    hole_end = hole_start
    for x in range(hole_start, w):
        if img.pixelColor(x, cy).alpha() >= 64:
            break
        hole_end = x
    return max(0, hole_end - hole_start + 1)


def _update_widget_side() -> int:
    glow = _check_button_glow()
    if glow.isNull():
        return _UPDATE_SIDE + 16
    return glow.width()


def _check_button_glow() -> QPixmap:
    """Pad-trimmed CheckButtonGlow scaled so the 32px hole matches the 56px plate."""
    global _GLOW_CACHE
    if _GLOW_CACHE is not None:
        return _GLOW_CACHE
    path = theme_file(_GLOW_NAME)
    if not path.is_file():
        path = _GLOW_EXTERNAL
    if not path.is_file():
        _GLOW_CACHE = QPixmap()
        return _GLOW_CACHE
    src = QPixmap(str(path))
    if src.isNull():
        _GLOW_CACHE = QPixmap()
        return _GLOW_CACHE
    img = src.toImage()
    pad = _alpha_bounds(img, _GLOW_PAD_ALPHA)
    if pad.isValid() and pad != src.rect():
        src = src.copy(pad)
        img = src.toImage()
    hole = _inner_hole_width(img) or 32
    dest = max(_UPDATE_SIDE + 16, int(round(src.width() * (_UPDATE_SIDE / hole))))
    _GLOW_CACHE = src.scaled(
        dest,
        dest,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return _GLOW_CACHE


class LaunchButton(QPushButton):
    """PLAY / INSTALL / REGISTER chrome button (objectName PlayButton).

    Uses taller WoW Glue-Panel art: red fill is shifted to the site bronze, then a
    bottom-weighted gradient and gold underline are painted the same way
    (per-pixel). Hover stays on the Up plate; Down art is click-only.
    """

    def __init__(
        self,
        text: str = "PLAY",
        parent=None,
        *,
        width: int = _PLAY_W,
        height: int = _PLAY_H,
        object_name: str = "PlayButton",
        square_chrome: bool = False,
    ):
        super().__init__(text, parent)
        self.setObjectName(object_name)
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(width, height)
        # Let paintEvent own the look — strip QSS chrome for this widget.
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setStyleSheet(
            f"QPushButton#{object_name} {{"
            "  background: transparent;"
            "  border: none;"
            "  padding: 0;"
            "  margin: 0;"
            "  color: transparent;"
            "}"
        )
        self._square_chrome = bool(square_chrome)
        self._chrome: QPixmap | None = None
        self._chrome_pressed: QPixmap | None = None
        self._chrome_disabled: QPixmap | None = None
        self._load_chrome()

    def _load_chrome(self) -> None:
        up = launch_glue_chrome(pressed=False, square=self._square_chrome)
        down = launch_glue_chrome(pressed=True, square=self._square_chrome)
        dead = launch_glue_chrome(
            pressed=False, disabled=True, square=self._square_chrome
        )
        if up.isNull():
            self._chrome = None
            self._chrome_pressed = None
            self._chrome_disabled = None
            return
        self._chrome = up
        self._chrome_pressed = down if not down.isNull() else up
        self._chrome_disabled = dead if not dead.isNull() else up

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        rect = self.rect()
        chrome = self._pick_chrome()
        if chrome is not None and not chrome.isNull():
            # Slight press inset for tactile feel
            draw_rect = rect.adjusted(1, 2, -1, 0) if self.isDown() else rect
            painter.drawPixmap(draw_rect, chrome)
        else:
            self._paint_fallback_chrome(painter, rect)

        self._paint_label(painter, rect)
        painter.end()

    def _pick_chrome(self) -> QPixmap | None:
        if not self.isEnabled():
            return self._chrome_disabled or self._chrome
        if self.isDown():
            return self._chrome_pressed or self._chrome
        return self._chrome

    def _paint_fallback_chrome(self, painter: QPainter, rect: QRect) -> None:
        """Vector fallback if the chrome PNG is missing."""
        outer = rect.adjusted(2, 2, -2, -2)
        painter.setPen(QColor(90, 78, 70))
        painter.setBrush(QColor(28, 24, 22))
        painter.drawRoundedRect(outer, 6, 6)
        inner = outer.adjusted(8, 8, -8, -8)
        painter.setPen(QColor(160, 130, 70, 180))
        painter.setBrush(QColor(18, 14, 12))
        painter.drawRoundedRect(inner, 3, 3)
        # bottom glow
        glow = QColor(_PLATE_TINT)
        for i in range(18):
            glow.setAlpha(max(0, 140 - i * 8))
            y = inner.bottom() - i
            painter.fillRect(inner.left() + 4, y, inner.width() - 8, 1, glow)

    def _paint_label(self, painter: QPainter, rect: QRect) -> None:
        text = (self.text() or "").upper()
        font = QFont(self.font())
        font.setFamily(chrome_family())
        font.setBold(True)
        # Longer labels need a smaller size; compact Home links also shrink to width.
        n = len(text.replace(" ", ""))
        if n >= 12:
            px, spacing = 13, 1.0
        elif n >= 9:
            px, spacing = 16, 1.6
        else:
            px, spacing = 20, 2.5
        inner_w = max(8, rect.width() - _LABEL_H_PAD)
        inner_h = max(8, rect.height() - _LABEL_V_PAD)
        words = text.split()
        start_px, start_spacing = px, spacing
        wrap = False

        def _apply(size: int, track: float) -> None:
            font.setPixelSize(size)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, track)

        def _fits(sample: str, size: int, track: float, lines: int = 1) -> bool:
            _apply(size, track)
            fm = QFontMetrics(font)
            if fm.horizontalAdvance(sample) > inner_w:
                return False
            # Measure ink, not just the line box. A display face puts flourishes
            # and descenders outside its reported height, and a container sized
            # from the metrics alone clips them - which is exactly what
            # ravencraft.io does to its own headings, where .folkard throws away
            # its line-height and inherits one computed for a plainer face.
            ink = max(fm.height(), fm.tightBoundingRect(sample).height())
            return ink * lines <= inner_h

        while spacing > 0 and not _fits(text, px, spacing):
            spacing = max(0.0, spacing - 0.25)
        while px > 12 and not _fits(text, px, spacing):
            px -= 1
        if not _fits(text, px, spacing) and len(words) > 1:
            wrap = True
            px, spacing = start_px, start_spacing
            longest = max(words, key=len)
            while spacing > 0 and not _fits(longest, px, spacing):
                spacing = max(0.0, spacing - 0.25)
            while px >= 10 and not _fits(longest, px, spacing):
                px -= 1
        elif not _fits(text, px, spacing):
            while px >= 8 and not _fits(text, px, spacing):
                px -= 1
        # Grow into the plate. Everything above only ever shrank, so PLAY sat at
        # 20px in a 56px button with most of the plate empty. The same two
        # bounds apply going up, so a longer label simply stops sooner.
        sample = max(words, key=len) if wrap else text
        lines = 2 if wrap else 1
        while px < _LABEL_MAX_PX and _fits(sample, px + 1, spacing, lines):
            px += 1

        _apply(px, spacing)
        painter.setFont(font)

        if not self.isEnabled():
            color = _MUTED
        elif self.isDown():
            color = _GOLD_SOFT
        else:
            color = _GOLD

        # Soft text shadow for recessed metal look
        text_rect = rect.adjusted(0, 0 if not self.isDown() else 1, 0, 0)
        # Optically centre: AlignCenter would centre the line box, which sits
        # the capitals high in a face that reserves ascent for flourishes.
        if not wrap:
            text_rect = ink_centered_rect(text_rect, font, text)
        text_rect = text_rect.translated(0, _LABEL_NUDGE_Y)
        shadow = QColor(0, 0, 0, 160)
        flags = Qt.AlignmentFlag.AlignCenter
        draw = "\n".join(words) if wrap else text
        if wrap:
            flags |= Qt.TextFlag.TextWordWrap
        painter.setPen(shadow)
        painter.drawText(text_rect.adjusted(1, 2, 1, 2), flags, draw)
        painter.setPen(color)
        painter.drawText(text_rect, flags, draw)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()


class UpdateLaunchButton(LaunchButton):
    """Square PLAY-chrome sibling. Up-arrow glyph; CheckButtonGlow pulses while pending."""

    def __init__(self, parent=None):
        side = _update_widget_side()
        super().__init__(
            "",
            parent,
            width=side,
            height=side,
            object_name="UpdateLaunchButton",
            square_chrome=True,
        )
        self.setToolTip("Update IchaLaunch")
        self.setAccessibleName("Update IchaLaunch")
        self.setVisible(False)
        self._glow = _check_button_glow()
        self._pulse = 0.0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(40)
        self._pulse_timer.timeout.connect(self._tick_pulse)

    def _chrome_rect(self) -> QRect:
        side = min(_UPDATE_SIDE, self.width(), self.height())
        return QRect(
            (self.width() - side) // 2,
            (self.height() - side) // 2,
            side,
            side,
        )

    def set_pending(self, pending: bool) -> None:
        """Show and pulse when a launcher update is available."""
        self.setVisible(bool(pending))
        if pending and self.isEnabled():
            if not self._pulse_timer.isActive():
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self._pulse = 0.0
            self.update()

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        super().setEnabled(enabled)
        if enabled and self.isVisible():
            if not self._pulse_timer.isActive():
                self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
            self.update()

    def _tick_pulse(self) -> None:
        self._pulse = (self._pulse + 0.10) % (2 * math.pi)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        chrome_rect = self._chrome_rect()
        if self.isEnabled() and not self._glow.isNull():
            self._paint_glow(painter)
        chrome = self._pick_chrome()
        if chrome is not None and not chrome.isNull():
            draw_rect = (
                chrome_rect.adjusted(1, 2, -1, 0) if self.isDown() else chrome_rect
            )
            painter.drawPixmap(draw_rect, chrome)
        else:
            self._paint_fallback_chrome(painter, chrome_rect)

        self._paint_arrow(painter, chrome_rect)
        painter.end()

    def _paint_glow(self, painter: QPainter) -> None:
        """CheckButtonGlow halo; opacity pulses while an update is waiting."""
        wave = 0.5 + 0.5 * math.sin(self._pulse)
        if self.underMouse() or self.isDown():
            opacity = 0.95
        else:
            opacity = 0.40 + 0.55 * wave
        painter.setOpacity(opacity)
        dest = self.rect()
        glow = self._glow
        if glow.width() != dest.width() or glow.height() != dest.height():
            x = dest.center().x() - glow.width() // 2
            y = dest.center().y() - glow.height() // 2
            painter.drawPixmap(x, y, glow)
        else:
            painter.drawPixmap(dest, glow)
        painter.setOpacity(1.0)

    def _paint_arrow(self, painter: QPainter, rect: QRect) -> None:
        arrow = _up_stream_arrow()
        cy_shift = 1 if self.isDown() else 0
        if arrow.isNull():
            self._paint_arrow_fallback(painter, rect, cy_shift)
            return
        if not self.isEnabled():
            painter.setOpacity(0.45)
        x = rect.center().x() - arrow.width() // 2
        y = rect.center().y() - arrow.height() // 2 + cy_shift
        painter.drawPixmap(x, y, arrow)
        painter.setOpacity(1.0)

    def _paint_arrow_fallback(self, painter: QPainter, rect: QRect, cy_shift: int) -> None:
        cx = rect.center().x()
        cy = rect.center().y() + cy_shift
        color = _MUTED if not self.isEnabled() else (_GOLD_SOFT if self.isDown() else _GOLD)
        head = QPainterPath()
        head.moveTo(cx, cy - 13)
        head.lineTo(cx + 9, cy - 2)
        head.lineTo(cx - 9, cy - 2)
        head.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(head)


def _up_stream_arrow() -> QPixmap:
    """UI-MicroStream-Yellow flipped so the chevron points up."""
    global _STREAM_ARROW_CACHE
    if _STREAM_ARROW_CACHE is not None:
        return _STREAM_ARROW_CACHE
    path = theme_file(_STREAM_ARROW)
    if not path.is_file():
        path = _STREAM_ARROW_FALLBACK
    if not path.is_file():
        _STREAM_ARROW_CACHE = QPixmap()
        return _STREAM_ARROW_CACHE
    src = QPixmap(str(path))
    if src.isNull():
        _STREAM_ARROW_CACHE = QPixmap()
        return _STREAM_ARROW_CACHE
    flipped = QPixmap.fromImage(src.toImage().mirrored(False, True))
    _STREAM_ARROW_CACHE = flipped.scaled(
        _STREAM_ARROW_PX,
        _STREAM_ARROW_PX,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return _STREAM_ARROW_CACHE
