"""Line edits framed with WoW CastingBar border chrome."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLineEdit, QSizePolicy

from ichalaunch.core.paths import theme_file

_BORDER_NAME = "UI-CastingBar-Border-Small.PNG"
_BORDER_EXTERNAL = Path(r"F:\wow-ui-textures\CastingBar\UI-CastingBar-Border-Small.PNG")

# Cropped rail (opaque bounds of the PNG) — hollow center, ornate L/R caps.
_SRC_H = 22
_CAP_SRC_W = 24
_MID_TILE_SRC_W = 16

_FILL = QColor(28, 24, 34, 210)
_FILL_FOCUS = QColor(36, 28, 48, 230)
_FALLBACK_BORDER = QColor(124, 92, 196, 160)
_FALLBACK_FOCUS = QColor("#F1C22D")

# ~10% taller than previous 37px rail so frame art scales with height.
_DEFAULT_MIN_H = 41
# Settings path/token bars — ≈25% taller than catalog/client search (41).
SETTINGS_MIN_H = 51

_CACHE: QPixmap | None = None


def _opaque_bounds(pm: QPixmap, alpha_min: int = 20) -> QRect:
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


def _load_border() -> QPixmap:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    path = theme_file(_BORDER_NAME)
    if not path.is_file():
        path = _BORDER_EXTERNAL
    pm = QPixmap(str(path)) if path.is_file() else QPixmap()
    if not pm.isNull():
        crop = _opaque_bounds(pm)
        if not crop.isNull() and crop != pm.rect():
            pm = pm.copy(crop)
    _CACHE = pm
    return pm


def _draw_casting_bar_frame(painter: QPainter, frame: QPixmap, dest: QRect) -> None:
    """Paint CastingBar border: fixed-aspect end caps + tiled middle rail."""
    if frame.isNull() or dest.width() < 8 or dest.height() < 8:
        return
    src_w = frame.width()
    src_h = frame.height() or _SRC_H
    scale = dest.height() / float(src_h)
    cap_w = _CAP_SRC_W * scale
    max_cap = dest.width() * 0.28
    if cap_w > max_cap:
        cap_w = max_cap
    cap_src = max(1, int(round(_CAP_SRC_W * (cap_w / max(1e-6, _CAP_SRC_W * scale)))))
    cap_src = min(cap_src, src_w // 3)

    left = QRect(dest.left(), dest.top(), int(round(cap_w)), dest.height())
    right = QRect(
        int(round(dest.right() - cap_w + 1)),
        dest.top(),
        int(round(cap_w)),
        dest.height(),
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
                dest.top(),
                max(1, int(round(tw))),
                dest.height(),
            ),
            frame,
            QRect(mid_src_x, 0, sw, src_h),
        )
        x += tw


class CastingBarSearchEdit(QLineEdit):
    """QLineEdit with CastingBar border chrome (transparent QSS + custom paint).

    Used for search fields and Settings path/token displays. Keeps normal text,
    placeholder, optional clear-button, and signal behavior.
    """

    def __init__(
        self,
        parent=None,
        *,
        object_name: str | None = "CastingBarSearch",
        read_only: bool = False,
        clear_button: bool = False,
        minimum_height: int = _DEFAULT_MIN_H,
    ):
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(minimum_height)
        self.setReadOnly(read_only)
        # Own the chrome — strip default QSS plate so paintEvent draws the frame.
        name = self.objectName() or "CastingBarSearch"
        self.setStyleSheet(
            f"QLineEdit#{name} {{"
            "  background: transparent;"
            "  border: none;"
            "  border-radius: 0;"
            "  padding: 0px;"
            "  color: #e6e0ee;"
            "  selection-background-color: #7c5cc4;"
            "}"
            f"QLineEdit#{name}:hover,"
            f"QLineEdit#{name}:focus {{"
            "  background: transparent;"
            "  border: none;"
            "}"
        )
        if clear_button:
            self.setClearButtonEnabled(True)
        else:
            self._sync_text_margins()
        _load_border()

    def setClearButtonEnabled(self, enable: bool) -> None:  # noqa: N802
        super().setClearButtonEnabled(enable)
        self._sync_text_margins()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_text_margins()

    def _sync_text_margins(self) -> None:
        # Prefer laid-out height; ignore huge pre-show default geometry (e.g. 640×480).
        h = self.height()
        if h < 20 or h > 80:
            h = max(self.minimumHeight(), _DEFAULT_MIN_H)
        # Keep glyphs inside the hollow rail; clear button needs extra right inset.
        inset_x = max(16, int(round(h * 0.48)))
        inset_y = max(4, int(round(h * 0.16)))
        clear_extra = 18 if self.isClearButtonEnabled() else 0
        self.setTextMargins(inset_x, inset_y, inset_x + clear_extra, inset_y)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self.rect()

        fill = _FILL_FOCUS if self.hasFocus() else _FILL
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        # Inset so fill sits inside the metal rail, not under the ornate ends.
        inset = max(3, int(round(min(rect.height(), 64) * 0.12)))
        painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), 4, 4)
        painter.end()

        # Text, placeholder, cursor, and clear button (style panel is transparent).
        super().paintEvent(event)

        # Frame last — hollow center leaves glyphs readable; chrome sits above QSS.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        frame = _load_border()
        if frame.isNull():
            pen = QPen(_FALLBACK_FOCUS if self.hasFocus() else _FALLBACK_BORDER)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)
        else:
            _draw_casting_bar_frame(painter, frame, self.rect())
        painter.end()
