"""WoW-style themed loading bar for the bottom play bar (RavenCraft purple fill)."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from ichalaunch.core.paths import theme_file

# Absolute-path fallback when bundled theme copies are missing (dev machines).
_FALLBACK_DIR = Path(r"F:\wow-ui-textures\GLUES\LoadingBar")

_BORDER = "Loading-BarBorder-Frame-v2.PNG"
_BACKGROUND = "Loading-BarBorder-Background-v2.PNG"
_FILL = "Loading-BarFill.PNG"
_SPARK = "UI-LoadingBar-Spark.PNG"

# RavenCraft purple shift for fill only (spark stays natural glow).
_FILL_MULTIPLY = QColor("#7c5cc4")
_FILL_GLAZE = QColor(74, 47, 122, 90)  # #4a2f7a warm dark purple

# Frame-v2 is a hollow 1024×64 rail with ~40px ornate end caps and a near-uniform
# middle. Draw as end-caps + horizontally tiled center (never stretch the full
# texture into a wide strip).
_FRAME_SRC_H = 64
_CAP_SRC_W = 40
_MID_TILE_SRC_W = 32

# Inner trough relative to drawn frame height (hollow interior of Frame-v2).
_INSET_T = 0.30
_INSET_B = 0.30


def _resolve_asset(name: str) -> Path | None:
    bundled = theme_file("loadingbar", name)
    if bundled.is_file():
        return bundled
    fallback = _FALLBACK_DIR / name
    if fallback.is_file():
        return fallback
    return None


def _load_pixmap(name: str) -> QPixmap:
    path = _resolve_asset(name)
    if path is None:
        return QPixmap()
    pm = QPixmap(str(path))
    return pm if not pm.isNull() else QPixmap()


def _opaque_bounds(pm: QPixmap, alpha_min: int = 20) -> QRect:
    """Tight rect of non-transparent pixels (for cropping padded GLUES textures)."""
    if pm.isNull():
        return QRect()
    img = pm.toImage()
    w, h = img.width(), img.height()
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if img.pixelColor(x, y).alpha() >= alpha_min:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if max_x < min_x:
        return QRect(0, 0, w, h)
    return QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def _tint_pixmap(src: QPixmap, multiply: QColor, glaze: QColor | None = None) -> QPixmap:
    """Multiply + optional SourceAtop glaze; preserves source alpha."""
    if src.isNull():
        return QPixmap()
    out = QPixmap(src.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawPixmap(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
    p.fillRect(out.rect(), multiply)
    if glaze is not None and glaze.alpha() > 0:
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        p.fillRect(out.rect(), glaze)
    p.end()
    return out


def _draw_frame_tiled(painter: QPainter, frame: QPixmap, dest: QRectF) -> None:
    """Paint Frame-v2: fixed-aspect end caps + tiled middle (height-correct scale)."""
    if frame.isNull() or dest.width() < 4 or dest.height() < 4:
        return
    src_w = frame.width()
    src_h = frame.height() or _FRAME_SRC_H
    scale = dest.height() / float(src_h)
    cap_w = _CAP_SRC_W * scale
    # Keep room for a middle segment.
    max_cap = dest.width() * 0.22
    if cap_w > max_cap:
        cap_w = max_cap
    cap_src = max(1, int(round(_CAP_SRC_W * (cap_w / max(1e-6, _CAP_SRC_W * scale)))))
    cap_src = min(cap_src, src_w // 3)

    left = QRect(
        int(round(dest.left())),
        int(round(dest.top())),
        int(round(cap_w)),
        int(round(dest.height())),
    )
    right = QRect(
        int(round(dest.right() - cap_w + 1)),
        int(round(dest.top())),
        int(round(cap_w)),
        int(round(dest.height())),
    )
    painter.drawPixmap(left, frame, QRect(0, 0, cap_src, src_h))
    painter.drawPixmap(right, frame, QRect(src_w - cap_src, 0, cap_src, src_h))

    mid_left = dest.left() + cap_w
    mid_right = dest.right() - cap_w + 1
    if mid_right <= mid_left:
        return

    tile_dst_w = max(1.0, _MID_TILE_SRC_W * scale)
    mid_src_x = max(0, (src_w - _MID_TILE_SRC_W) // 2)
    x = mid_left
    while x < mid_right - 0.5:
        tw = min(tile_dst_w, mid_right - x)
        sw = max(1, int(round(_MID_TILE_SRC_W * (tw / tile_dst_w))))
        sw = min(sw, src_w - mid_src_x)
        painter.drawPixmap(
            QRect(
                int(round(x)),
                int(round(dest.top())),
                max(1, int(round(tw))),
                int(round(dest.height())),
            ),
            frame,
            QRect(mid_src_x, 0, sw, src_h),
        )
        x += tw


class ThemeLoadingBar(QWidget):
    """Custom determinate / indeterminate progress bar using WoW GLUES art.

    API mirrors the bits of ``QProgressBar`` used by ``MainWindow`` so wiring
    stays a drop-in swap (``setRange`` / ``setValue`` / ``show`` / ``hide``).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BottomProgress")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(32)
        self.setMaximumHeight(40)
        # Default slot is the gap between status and PLAY. When the square
        # update button is showing, reserve_trailing() tightens this so the
        # rail does not run under the arrow.
        self.setMinimumWidth(320)
        self.setMaximumWidth(880)

        self._minimum = 0
        self._maximum = 100
        self._value = 0
        self._format = "%p%"
        self._text_visible = False

        self._border = _load_pixmap(_BORDER)
        self._background = _load_pixmap(_BACKGROUND)
        # BG art has ~31px transparent L/R padding — crop so stretch fills the trough.
        self._bg_src = _opaque_bounds(self._background)
        if self._bg_src.isNull() and not self._background.isNull():
            self._bg_src = self._background.rect()
        self._fill = _tint_pixmap(_load_pixmap(_FILL), _FILL_MULTIPLY, _FILL_GLAZE)
        # Natural spark glow — no purple multiply/glaze.
        self._spark = _load_pixmap(_SPARK)

        # Indeterminate: gently pulse fill width + spark (downloads without %).
        self._indeterminate = False
        self._pulse_phase = 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(33)
        self._anim.timeout.connect(self._on_anim_tick)

        self.hide()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(min(640, self.maximumWidth()), 36)

    def reserve_trailing(self, px: int = 0) -> None:
        """Shrink the bar's min/max width by *px* (update-button slot)."""
        extra = max(0, int(px))
        self.setMinimumWidth(220 if extra else 320)
        self.setMaximumWidth(max(self.minimumWidth(), 880 - extra))
        self.updateGeometry()

    # ---- QProgressBar-compatible surface ---------------------------------

    def setRange(self, minimum: int, maximum: int) -> None:
        self._minimum = int(minimum)
        self._maximum = int(maximum)
        indeterminate = maximum <= minimum
        if indeterminate != self._indeterminate:
            self._indeterminate = indeterminate
            if self._indeterminate and self.isVisible():
                self._pulse_phase = 0.0
                self._anim.start()
            elif not self._indeterminate:
                self._anim.stop()
        self.update()

    def setValue(self, value: int) -> None:
        self._value = int(value)
        if not self._indeterminate:
            self.update()

    def value(self) -> int:
        return self._value

    def maximum(self) -> int:
        return self._maximum

    def minimum(self) -> int:
        return self._minimum

    def setFormat(self, fmt: str) -> None:
        self._format = fmt or ""
        self.update()

    def setTextVisible(self, visible: bool) -> None:
        self._text_visible = bool(visible)
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._indeterminate and not self._anim.isActive():
            self._anim.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._anim.stop()
        super().hideEvent(event)

    # ---- painting --------------------------------------------------------

    def _on_anim_tick(self) -> None:
        self._pulse_phase = (self._pulse_phase + 0.045) % (2.0 * math.pi)
        self.update()

    def _progress_fraction(self) -> float:
        if self._indeterminate:
            wave = 0.5 + 0.5 * math.sin(self._pulse_phase)
            return 0.18 + 0.37 * wave
        span = self._maximum - self._minimum
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (self._value - self._minimum) / span))

    def _frame_metrics(self, border: QRectF) -> tuple[float, QRectF]:
        """Return (cap_width_px, trough_rect) for the current frame draw."""
        src_h = self._border.height() if not self._border.isNull() else _FRAME_SRC_H
        scale = border.height() / float(src_h or _FRAME_SRC_H)
        cap_w = _CAP_SRC_W * scale
        max_cap = border.width() * 0.22
        if cap_w > max_cap:
            cap_w = max_cap
        # Sit just inside the end-cap ornament so bg/fill reach the frame ends.
        inset_x = max(3.0, cap_w * 0.50)
        trough = QRectF(
            border.left() + inset_x,
            border.top() + border.height() * _INSET_T,
            max(1.0, border.width() - 2 * inset_x),
            border.height() * (1.0 - _INSET_T - _INSET_B),
        )
        return cap_w, trough

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        border_rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        _cap_w, trough = self._frame_metrics(border_rect)

        # Background trough — stretch opaque content only (ignore PNG side padding).
        if not self._background.isNull():
            dest = trough.toRect()
            # Nudge 1px past trough edges to hide rounding gaps under the frame.
            dest.adjust(-1, 0, 1, 0)
            src = self._bg_src if not self._bg_src.isNull() else self._background.rect()
            painter.drawPixmap(dest, self._background, src)
        else:
            painter.fillRect(trough, QColor(16, 10, 22, 200))

        frac = self._progress_fraction()
        fill_w = trough.width() * frac
        spark_x = trough.left()

        if fill_w > 0.5 and not self._fill.isNull():
            fill_rect = QRectF(trough.left(), trough.top(), fill_w, trough.height())
            painter.drawPixmap(fill_rect.toRect(), self._fill)
            spark_x = fill_rect.right()
        elif fill_w > 0.5:
            fill_rect = QRectF(trough.left(), trough.top(), fill_w, trough.height())
            painter.fillRect(fill_rect, QColor(74, 47, 122, 210))
            spark_x = fill_rect.right()

        # Frame-v2: end caps + tiled middle (not a single stretched strip).
        if not self._border.isNull():
            _draw_frame_tiled(painter, self._border, border_rect)
        else:
            painter.setPen(QColor(124, 92, 196, 160))
            painter.drawRect(border_rect)

        # Spark at leading edge — natural PNG glow only.
        if frac > 0.01 and not self._spark.isNull():
            spark_h = trough.height() * 1.55
            spark_w = spark_h * (self._spark.width() / max(1, self._spark.height()))
            spark_rect = QRectF(
                spark_x - spark_w * 0.5,
                trough.center().y() - spark_h * 0.5,
                spark_w,
                spark_h,
            )
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawPixmap(spark_rect.toRect(), self._spark)

        if self._text_visible and self._format and not self._indeterminate:
            text = self._format.replace("%p", str(int(round(frac * 100)))).replace(
                "%v", str(self._value)
            )
            painter.setPen(QColor("#e6e0ee"))
            painter.drawText(border_rect.toRect(), Qt.AlignmentFlag.AlignCenter, text)
