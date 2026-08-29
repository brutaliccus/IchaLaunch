"""PLAY-bar realm status: radio-ring chrome with a quality-colored hole fill."""

from __future__ import annotations

import math
from collections import deque
from typing import NamedTuple

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from ichalaunch.game.realm_status import RealmProbe, tooltip_for
from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.widgets.theme_radio import (
    _DOT_FIT_SCALE,
    _INDICATOR_PX,
    _OFF,
    _knockout_near_black,
    _load_raw,
    _slice_atlas,
)
from ichalaunch.ui.widgets.wow_tooltip import tooltip_font

# Always-visible PLAY-bar reserve for UPDATE (see MainWindow). The ping is an
# overlay to the right of PLAY and does not grow the cluster.
PING_DOT_SIZE = 22
PING_DOT_GAP = 8
# Transparent well inside UI-RadioButton-Off (same idea as ThemeRadio knockout).
_HOLE_ALPHA = 24
# 1 native pixel of well between the orb and the silver (classic On radio).
_ORB_INSET_PX = 1

_COLORS = {
    "green": QColor("#3DCC6D"),
    "yellow": QColor("#F1C22D"),
    "red": QColor("#E24B4B"),
    "grey": QColor("#6B6570"),
}
# Same ink as contributor tooltip / launch gold. Hover is text-only (no plate).
_TIP_GOLD = QColor("#F1C22D")
_TIP_SHADOW = QColor(0, 0, 0, 200)
_TIP_GAP = 3
_TIP_SHADOW_PX = 1


class _RadioChrome(NamedTuple):
    ring: QPixmap
    hole_mask: QImage
    native_hole_d: int


_CHROME: _RadioChrome | None = None


def ping_overlay_x(play_right: int, border_right: int, ping_width: int) -> int:
    """Left edge so the ping's center sits at the midpoint of [play_right, border_right]."""
    mid = (play_right + border_right) / 2.0
    return int(round(mid - ping_width / 2.0))


def _raw_off_ring() -> QPixmap:
    raw = _load_raw(_OFF)
    if raw.isNull():
        raw = _slice_atlas(0)
    return _knockout_near_black(raw)


def _flood_hole_mask(img: QImage, max_alpha: int = _HOLE_ALPHA) -> QImage:
    """White where the Off ring's inner well is transparent."""
    w, h = img.width(), img.height()
    mask = QImage(w, h, QImage.Format.Format_ARGB32)
    mask.fill(QColor(0, 0, 0, 0))
    if w <= 0 or h <= 0:
        return mask
    cx, cy = w // 2, h // 2
    if img.pixelColor(cx, cy).alpha() >= max_alpha:
        return mask
    seen = bytearray(w * h)
    q: deque[tuple[int, int]] = deque([(cx, cy)])
    seen[cy * w + cx] = 1
    white = QColor(255, 255, 255, 255)
    while q:
        x, y = q.popleft()
        mask.setPixelColor(x, y, white)
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx]:
                seen[ny * w + nx] = 1
                if img.pixelColor(nx, ny).alpha() < max_alpha:
                    q.append((nx, ny))
    return mask


def _ellipse_hole_mask(width: int, height: int) -> tuple[QImage, QRect]:
    """ThemeRadio inner-orb inset (~64%) when the flood leaks out of the well."""
    mask = QImage(width, height, QImage.Format.Format_ARGB32)
    mask.fill(QColor(0, 0, 0, 0))
    inset = max(1, int(round(width * (1.0 - _DOT_FIT_SCALE) / 2.0)))
    inner = QRect(
        inset,
        inset,
        max(1, width - 2 * inset),
        max(1, height - 2 * inset),
    )
    painter = QPainter(mask)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 255))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.drawEllipse(inner)
    painter.end()
    return mask, inner


def _mask_bounds(mask: QImage) -> QRect:
    w, h = mask.width(), mask.height()
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if mask.pixelColor(x, y).alpha() >= 20:
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


def _mask_centroid(mask: QImage) -> QPointF:
    w, h = mask.width(), mask.height()
    sx = sy = n = 0
    for y in range(h):
        for x in range(w):
            if mask.pixelColor(x, y).alpha() >= 20:
                sx += x
                sy += y
                n += 1
    if n <= 0:
        return QPointF(w / 2.0, h / 2.0)
    return QPointF(sx / n + 0.5, sy / n + 0.5)


def _inscribed_radius(mask: QImage, cx: float, cy: float) -> float:
    """Distance from the hole center to the nearest silver / non-well pixel."""
    w, h = mask.width(), mask.height()
    if w <= 0 or h <= 0:
        return 0.0
    best = min(cx, cy, (w - cx), (h - cy))
    r2 = best * best
    for y in range(h):
        for x in range(w):
            if mask.pixelColor(x, y).alpha() >= 20:
                continue
            dx = (x + 0.5) - cx
            dy = (y + 0.5) - cy
            d2 = dx * dx + dy * dy
            if d2 < r2:
                r2 = d2
    return max(0.0, math.sqrt(r2))


def _hole_fill_rect(mask: QImage) -> QRect:
    """Widget-space rect of the inscribed, 1px-inset orb. Used by paint and tests."""
    if mask.isNull() or mask.width() <= 0:
        return QRect()
    c = _mask_centroid(mask)
    r = _inscribed_radius(mask, c.x(), c.y())
    dest_d = max(1, int(round(2.0 * r)))
    x = int(round(c.x() - dest_d / 2.0))
    y = int(round(c.y() - dest_d / 2.0))
    return QRect(x, y, dest_d, dest_d)


def _scale_chrome(ring: QPixmap, mask: QImage, side: int) -> tuple[QPixmap, QImage]:
    # Nearest-neighbor so the silver ring stays as chunky as the source art.
    scaled_ring = ring.scaled(
        side,
        side,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    scaled_mask = QPixmap.fromImage(mask).scaled(
        side,
        side,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    ).toImage()
    return scaled_ring, scaled_mask


def _radio_chrome() -> _RadioChrome:
    """Client-tab Off ring + hole mask, sized like ThemeRadio (22px)."""
    global _CHROME
    if _CHROME is not None:
        return _CHROME
    raw = _raw_off_ring()
    if raw.isNull():
        _CHROME = _RadioChrome(QPixmap(), QImage(), 0)
        return _CHROME
    mask = _flood_hole_mask(raw.toImage())
    hole = _mask_bounds(mask)
    hole_d = min(hole.width(), hole.height()) if hole.isValid() else 0
    # Knockout can punch ring recesses and leak the flood — treat a near-full
    # mask as a miss and use ThemeRadio's 64% inner-orb inset instead.
    if hole_d <= 0 or hole_d > raw.width() * 0.85:
        mask, hole = _ellipse_hole_mask(raw.width(), raw.height())
        hole_d = min(hole.width(), hole.height())
    native_hole_d = max(1, hole_d)
    side = max(PING_DOT_SIZE, _INDICATOR_PX)
    ring, mask = _scale_chrome(raw, mask, side)
    _CHROME = _RadioChrome(ring, mask, native_hole_d)
    return _CHROME


def _orb_source_side(native_d: int, dest_d: int) -> int:
    """Medium pixel-art grid: ~2× native hole, clamped to [native, dest].

    Halfway between native and dest, biased toward dest, when dest is only
    slightly larger than native (2× would exceed dest).
    """
    native_d = max(1, int(native_d))
    dest_d = max(1, int(dest_d))
    twice = native_d * 2
    if native_d < dest_d < twice:
        source = (native_d + dest_d * 2 + 2) // 3
    else:
        source = twice
    return max(native_d, min(dest_d, source))


def _orb_base_color(color: QColor, dim: bool) -> QColor:
    if not dim:
        return QColor(color)
    hue, sat, val, alpha = color.getHsv()
    if hue < 0:
        return QColor.fromHsv(0, 0, max(0, int(val * 0.58)), alpha)
    return QColor.fromHsv(
        hue,
        max(0, int(sat * 0.38)),
        max(0, int(val * 0.58)),
        alpha,
    )


def _paint_orb_native(color: QColor, side: int, *, dim: bool) -> QImage:
    """Classic WoW radio orb on a native pixel grid (no anti-aliased disc)."""
    side = max(1, int(side))
    img = QImage(side, side, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    cx = (side - 1) / 2.0
    cy = (side - 1) / 2.0
    r = min(side, side) / 2.0 - _ORB_INSET_PX
    if r < 0.55:
        img.setPixelColor(side // 2, side // 2, _orb_base_color(color, dim))
        return img
    base = _orb_base_color(color, dim)
    hr, hg, hb = base.red(), base.green(), base.blue()
    # Upper-left key light — same side the silver ring bevels from.
    lx, ly, lz = -0.42, -0.52, 0.74
    length = math.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / length, ly / length, lz / length
    spec_boost = 0.35 if dim else 1.0
    rim_boost = 0.40 if dim else 1.0
    for y in range(side):
        for x in range(side):
            dx = (x - cx) / r
            dy = (y - cy) / r
            d2 = dx * dx + dy * dy
            if d2 > 1.0:
                continue
            nz = math.sqrt(max(0.0, 1.0 - d2))
            ndotl = max(0.0, dx * lx + dy * ly + nz * lz)
            shade = 0.34 + 0.66 * (ndotl * 0.72 + 0.28)
            r8 = min(255, int(hr * shade + 16 * ndotl))
            g8 = min(255, int(hg * shade + 16 * ndotl))
            b8 = min(255, int(hb * shade + 12 * ndotl))
            spec = ndotl ** 14
            if spec > 0.18:
                amt = min(1.0, (spec - 0.18) * 2.2) * spec_boost
                r8 = min(255, int(r8 + (235 - r8) * amt * 0.85))
                g8 = min(255, int(g8 + (240 - g8) * amt * 0.85))
                b8 = min(255, int(b8 + (220 - b8) * amt * 0.70))
            fres = (1.0 - nz) ** 2
            if fres > 0.35 and ndotl > 0.25:
                rim = (fres - 0.35) * 0.45 * rim_boost
                r8 = min(255, int(r8 + 40 * rim))
                g8 = min(255, int(g8 + 45 * rim))
                b8 = min(255, int(b8 + 35 * rim))
            img.setPixelColor(x, y, QColor(r8, g8, b8, 255))
    return img


class PingLatencyTip(QWidget):
    """Frameless gold text above the ping orb. A top-level popup so it can overflow."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._text = ""
        self._face = tooltip_font()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def set_text(self, text: str) -> None:
        self._text = (text or "").strip()
        self._face = tooltip_font(self.font())
        fm = QFontMetrics(self._face)
        name = self._text or " "
        # Extra pixels so the 1px shadow is not clipped.
        self.setFixedSize(
            max(1, fm.horizontalAdvance(name) + _TIP_SHADOW_PX + 2),
            max(1, fm.height() + _TIP_SHADOW_PX + 2),
        )

    def popup_above(self, anchor: QWidget) -> None:
        if not self._text or anchor is None:
            self.hide()
            return
        self.set_text(self._text)
        top_center = anchor.mapToGlobal(QPoint(anchor.width() // 2, 0))
        x = top_center.x() - self.width() // 2
        y = top_center.y() - self.height() - _TIP_GAP
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
        try:
            if not painter.isActive():
                return
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setFont(self._face)
            rect = self.rect()
            painter.setPen(_TIP_SHADOW)
            painter.drawText(
                rect.adjusted(_TIP_SHADOW_PX, _TIP_SHADOW_PX, 0, 0),
                int(Qt.AlignmentFlag.AlignCenter),
                self._text,
            )
            painter.setPen(_TIP_GOLD)
            painter.drawText(
                rect.adjusted(0, 0, -_TIP_SHADOW_PX, -_TIP_SHADOW_PX),
                int(Qt.AlignmentFlag.AlignCenter),
                self._text,
            )
            painter.end()


        finally:
            if painter.isActive():
                painter.end()

class RealmPingDot(QWidget):
    """Radio-ring status to the right of PLAY. Hover shows the last logon RTT."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RealmPingDot")
        chrome = _radio_chrome()
        side = (
            max(PING_DOT_SIZE, chrome.ring.width(), chrome.ring.height())
            if not chrome.ring.isNull()
            else PING_DOT_SIZE
        )
        self.setFixedSize(side, side)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        apply_open_hand(self)
        self._probe: RealmProbe | None = None
        self._checking = True
        self._quality = "grey"
        self._latency_tip: PingLatencyTip | None = None
        self._apply_accessible()

    def sizeHint(self) -> QSize:
        return QSize(self.width() or PING_DOT_SIZE, self.height() or PING_DOT_SIZE)

    @property
    def quality(self) -> str:
        return self._quality

    def latency_text(self) -> str:
        return tooltip_for(self._probe, checking=self._checking)

    def set_probe(self, probe: RealmProbe) -> None:
        self._probe = probe
        self._checking = False
        self._quality = probe.quality if probe.quality in _COLORS else "grey"
        self._apply_accessible()
        if self._latency_tip is not None and self._latency_tip.isVisible():
            self._latency_tip.set_text(self.latency_text())
            self._latency_tip.popup_above(self)
        self.update()

    def set_offline(self, error: str | None = None) -> None:
        from ichalaunch.game.realm_status import offline_probe

        self.set_probe(offline_probe(error=error))

    def _apply_accessible(self) -> None:
        self.setAccessibleName(f"Realm {self.latency_text()}")

    def _ensure_tip(self) -> PingLatencyTip:
        if self._latency_tip is None:
            # Owned by the ping for lifetime, but ToolTip flags make it a
            # separate popup window so gold text can hang past the chrome.
            self._latency_tip = PingLatencyTip(self)
            self._latency_tip.destroyed.connect(self._clear_tip)
        return self._latency_tip

    def _clear_tip(self, *_args) -> None:
        self._latency_tip = None

    def enterEvent(self, event) -> None:  # noqa: ANN001, N802
        tip = self._ensure_tip()
        tip.set_text(self.latency_text())
        tip.popup_above(self)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._latency_tip is not None:
            self._latency_tip.dismiss()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._latency_tip is not None:
            self._latency_tip.dismiss()
        super().hideEvent(event)

    def _paint_fallback(self, painter: QPainter, color: QColor) -> None:
        dim = self._quality == "grey"
        native = max(4, int(round(PING_DOT_SIZE * _DOT_FIT_SCALE / 2)))
        orb = _paint_orb_native(color, native, dim=dim)
        dest = max(1, int(round(min(self.width(), self.height()) * _DOT_FIT_SCALE)))
        scaled = QPixmap.fromImage(orb).scaled(
            dest,
            dest,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        painter.drawPixmap(
            (self.width() - scaled.width()) // 2,
            (self.height() - scaled.height()) // 2,
            scaled,
        )

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        del event
        painter = QPainter(self)
        try:
            if not painter.isActive():
                return
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            color = _COLORS.get(self._quality, _COLORS["grey"])
            chrome = _radio_chrome()
            ring, hole_mask = chrome.ring, chrome.hole_mask
            if ring.isNull() or hole_mask.isNull() or _mask_bounds(hole_mask).isNull():
                self._paint_fallback(painter, color)
                painter.end()
                return

            ox = (self.width() - ring.width()) // 2
            oy = (self.height() - ring.height()) // 2
            dest = _hole_fill_rect(hole_mask)
            if dest.isNull() or dest.width() <= 0:
                self._paint_fallback(painter, color)
                painter.end()
                return

            native_d = max(3, chrome.native_hole_d)
            dest_d = max(dest.width(), dest.height())
            source_d = _orb_source_side(native_d, dest_d)
            orb = _paint_orb_native(color, source_d, dim=self._quality == "grey")
            scaled = QPixmap.fromImage(orb).scaled(
                dest.width(),
                dest.height(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            layer = QPixmap(ring.size())
            layer.fill(Qt.GlobalColor.transparent)
            lp = QPainter(layer)
            lp.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            lp.drawPixmap(dest.topLeft(), scaled)
            # Safety clip — orb is already inset; this keeps any scale crumbs off silver.
            lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            lp.drawImage(0, 0, hole_mask)
            lp.end()

            painter.drawPixmap(ox, oy, layer)
            painter.drawPixmap(ox, oy, ring)
            painter.end()
        finally:
            if painter.isActive():
                painter.end()
