"""Framed contributor portrait: photo clipped to a border hole or bright rim."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from ichalaunch.core.paths import theme_file
from ichalaunch.ui.widgets.common import (
    discord_user_id_from_url,
    open_discord_user_profile,
    open_url_in_browser,
)
from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.widgets.wow_tooltip import ContributorNameTip

# Default rim (contributor 1) — pink-tinted CheckButtonGlow.
_DEFAULT_BORDER = "CheckButtonGlow-Pink.PNG"
# Display size shared by every contributor icon in the play-bar row.
# Soft CheckButton* glow (46–56px pad-trimmed) fits fully; bright ring ~38px.
_DISPLAY = 52
# Glow margin for the portraits. Proportional to the plate's: 12px around a
# 200x56 plate is roughly 8 around a 52px portrait.
_GLOW_MARGIN = 9
_GLOW_HALO_PAD = 9
_GLOW_HALO_BLUR = 4
# CheckButtonHilight-Blue soft pad is larger than Glow, so fit-to-box leaves
# its bright ring ~38px vs Glow's ~42px. Slight overscale matches footprint;
# outer soft still reaches the box edge (only faint pad clips).
_HILIGHT_BLUE_SCALE = 1.08
# Pixels below this alpha count as the border's open center (photo region).
_HOLE_ALPHA_THR = 40
# Soft halo / pad trim — empty transparent margin only (glow stays).
_SOFT_ALPHA_THR = 8
# Bright opaque ring (CheckButtonHilight-Blue mid-row peaks a≈255; outer
# edge of the solid rim is ~a≥160). Photo fill/clip stops here — not in the
# soft glow (a 8–159) beyond the rim.
_BRIGHT_ALPHA_THR = 160
# Outer fill (contributor #3): pull cover + bright-ring clip 1px inside the
# opaque rim so the photo does not overhang the bright edge.
_OUTER_FILL_INSET = 1
# Circle cutout (contributor #2): Euclidean RGB distance from corner-sampled
# charcoal grey; edge flood-fill keys those pixels to transparent.
_GREY_KEY_DIST = 24.0


def _center_square_crop(pm: QPixmap) -> QPixmap:
    if pm.isNull():
        return pm
    w, h = pm.width(), pm.height()
    side = min(w, h)
    if side <= 0:
        return pm
    x = (w - side) // 2
    y = (h - side) // 2
    return pm.copy(x, y, side, side)


def _photo_for_hole(
    photo: QPixmap,
    hole_w: int,
    hole_h: int,
    *,
    crop_mode: str,
) -> QPixmap:
    """Scale photo into ``hole_w``×``hole_h``.

    ``contain`` — aspect-fit (full image visible; may letterbox).
    ``cover`` — center-square then expand-crop (fills hole; may chop edges).
    """
    if photo.isNull() or hole_w <= 0 or hole_h <= 0:
        return QPixmap()
    mode = (crop_mode or "contain").strip().lower()
    if mode == "cover":
        square = _center_square_crop(photo)
        scaled = square.scaled(
            hole_w,
            hole_h,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        sx = max(0, (scaled.width() - hole_w) // 2)
        sy = max(0, (scaled.height() - hole_h) // 2)
        return scaled.copy(sx, sy, hole_w, hole_h)
    # contain (default): fit entire photo inside the hole box
    return photo.scaled(
        hole_w,
        hole_h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


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


def _prepare_border(
    border: QPixmap, out_size: int, *, scale: float = 1.0
) -> tuple[QPixmap, int, int, int]:
    """Return (scaled_border_piece, draw_x, draw_y, piece_side).

    Pad-trims empty transparent margin, then scales the *full soft glow*
    (including CheckButtonGlow / Hilight / Pink halo) to ``out_size * scale``.
    ``scale`` 1.0 fills the box; values slightly above 1 nudge a thinner
    bright ring up to match siblings (centered; faint outer pad may clip).
    """
    if border.isNull():
        return QPixmap(), 0, 0, out_size

    piece = max(1, int(round(out_size * max(0.01, float(scale)))))
    ox = (out_size - piece) // 2
    oy = (out_size - piece) // 2

    img = border.toImage()
    soft = _alpha_bounds(img, _SOFT_ALPHA_THR)
    if not soft.isValid():
        framed = border.scaled(
            piece,
            piece,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        return framed, ox, oy, piece

    cropped = border.copy(soft)
    framed = cropped.scaled(
        piece,
        piece,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return framed, ox, oy, piece


def _flood_below_alpha(border: QPixmap, max_alpha: int) -> QImage:
    """Flood-fill from image center through pixels with alpha ``< max_alpha``.

    Returns an ARGB mask (white = inside) at the border's native size, or an
    empty transparent image if the center is already opaque.
    """
    if border.isNull():
        return QImage()

    src = border.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    w, h = src.width(), src.height()
    if w <= 0 or h <= 0:
        return QImage()

    cx, cy = w // 2, h // 2
    if src.pixelColor(cx, cy).alpha() >= max_alpha:
        return QImage()

    visited = [[False] * w for _ in range(h)]
    visited[cy][cx] = True
    q: deque[tuple[int, int]] = deque([(cx, cy)])
    cells: list[tuple[int, int]] = []
    while q:
        x, y = q.popleft()
        cells.append((x, y))
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h and not visited[ny][nx]:
                visited[ny][nx] = True
                if src.pixelColor(nx, ny).alpha() < max_alpha:
                    q.append((nx, ny))

    mask = QImage(w, h, QImage.Format.Format_ARGB32)
    mask.fill(QColor(0, 0, 0, 0))
    white = QColor(255, 255, 255, 255)
    for x, y in cells:
        mask.setPixelColor(x, y, white)
    return mask


def _center_hole_mask(border: QPixmap, out_size: int) -> QPixmap:
    """Opaque where the photo may draw — flood-fill the border's transparent center.

    Stops at the opaque rim, so soft outer glow (CheckButton*) does not leave
    photo pixels past the ring.
    """
    size = max(1, int(out_size))
    if border.isNull():
        full = QPixmap(size, size)
        full.fill(Qt.GlobalColor.white)
        return full

    src = border.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    w, h = src.width(), src.height()
    if w <= 0 or h <= 0:
        full = QPixmap(size, size)
        full.fill(Qt.GlobalColor.white)
        return full

    mask = _flood_below_alpha(border, _HOLE_ALPHA_THR)
    if mask.isNull() or mask.width() == 0:
        # No clear center hole — fall back to a modest uniform inset.
        inset = max(1, round(min(w, h) * 0.12))
        mask = QImage(w, h, QImage.Format.Format_ARGB32)
        mask.fill(QColor(0, 0, 0, 0))
        for y in range(inset, h - inset):
            for x in range(inset, w - inset):
                mask.setPixelColor(x, y, QColor(255, 255, 255, 255))

    return QPixmap.fromImage(mask).scaled(
        size,
        size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _outer_bright_mask(border: QPixmap, out_size: int) -> QPixmap:
    """Filled silhouette to bright-ring outer edge.

    Flood from center through everything below the bright threshold, then OR
    the bright rim itself (α≥160). That reaches the opaque ring's *outer*
    footprint without including the soft glow beyond it. Full border (incl.
    soft glow) is still drawn on top afterward.
    """
    size = max(1, int(out_size))
    if border.isNull():
        full = QPixmap(size, size)
        full.fill(Qt.GlobalColor.white)
        return full

    src = border.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    w, h = src.width(), src.height()
    if w <= 0 or h <= 0:
        full = QPixmap(size, size)
        full.fill(Qt.GlobalColor.white)
        return full

    mask = _flood_below_alpha(border, _BRIGHT_ALPHA_THR)
    if mask.isNull() or mask.width() == 0:
        # No clear center — fall back to bright-ring bbox fill.
        bright = _alpha_bounds(src, _BRIGHT_ALPHA_THR)
        mask = QImage(w, h, QImage.Format.Format_ARGB32)
        mask.fill(QColor(0, 0, 0, 0))
        if bright.isValid():
            for y in range(bright.top(), bright.bottom() + 1):
                for x in range(bright.left(), bright.right() + 1):
                    mask.setPixelColor(x, y, QColor(255, 255, 255, 255))
    else:
        white = QColor(255, 255, 255, 255)
        for y in range(h):
            for x in range(w):
                if src.pixelColor(x, y).alpha() >= _BRIGHT_ALPHA_THR:
                    mask.setPixelColor(x, y, white)

    return QPixmap.fromImage(mask).scaled(
        size,
        size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _inset_disk_mask(mask: QPixmap, inset: int) -> QPixmap:
    """Shrink an opaque disk/silhouette by ``inset`` px on all sides (centered)."""
    if mask.isNull() or inset <= 0:
        return mask
    w, h = mask.width(), mask.height()
    nw = w - 2 * inset
    nh = h - 2 * inset
    if nw <= 0 or nh <= 0:
        return mask
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    shrunk = mask.scaled(
        nw,
        nh,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    p = QPainter(out)
    p.drawPixmap(inset, inset, shrunk)
    p.end()
    return out


def _rgb_dist(c: QColor, br: int, bg: int, bb: int) -> float:
    dr = c.red() - br
    dg = c.green() - bg
    db = c.blue() - bb
    return (dr * dr + dg * dg + db * db) ** 0.5


def _key_edge_grey(photo: QPixmap, max_dist: float = _GREY_KEY_DIST) -> QPixmap:
    """Flood-fill from image edges through charcoal-grey; those pixels → α=0.

    Samples the four corner RGBs as the key color (source art uses ~#1A191E).
    Stops at pink/black circle strokes so only the square pad becomes transparent.
    """
    if photo.isNull():
        return photo

    src = photo.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    w, h = src.width(), src.height()
    if w <= 0 or h <= 0:
        return photo

    corners = (
        src.pixelColor(0, 0),
        src.pixelColor(w - 1, 0),
        src.pixelColor(0, h - 1),
        src.pixelColor(w - 1, h - 1),
    )
    br = sum(c.red() for c in corners) // 4
    bg = sum(c.green() for c in corners) // 4
    bb = sum(c.blue() for c in corners) // 4

    visited = [[False] * w for _ in range(h)]
    q: deque[tuple[int, int]] = deque()

    def try_seed(x: int, y: int) -> None:
        if visited[y][x]:
            return
        visited[y][x] = True
        if _rgb_dist(src.pixelColor(x, y), br, bg, bb) <= max_dist:
            q.append((x, y))

    for x in range(w):
        try_seed(x, 0)
        try_seed(x, h - 1)
    for y in range(h):
        try_seed(0, y)
        try_seed(w - 1, y)

    while q:
        x, y = q.popleft()
        src.setPixelColor(x, y, QColor(0, 0, 0, 0))
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h and not visited[ny][nx]:
                visited[ny][nx] = True
                if _rgb_dist(src.pixelColor(nx, ny), br, bg, bb) <= max_dist:
                    q.append((nx, ny))

    return QPixmap.fromImage(src)


def compose_contributor_portrait(
    photo_name: str,
    *,
    border_name: str | None = _DEFAULT_BORDER,
    display: int = _DISPLAY,
    crop_mode: str = "contain",
    border_scale: float = 1.0,
    fill_mode: str = "hole",
) -> QPixmap:
    """Photo scaled into the border, then border drawn on top.

    Soft glow borders are scaled to the shared display box (× ``border_scale``).

    ``crop_mode``:
      - ``contain`` (default) — aspect-fit inside the fill box; full photo visible
      - ``cover`` — fill the box (center-crop); may chop edges

    ``fill_mode``:
      - ``hole`` (default) — scale into the transparent center; clip at rim
      - ``outer`` — scale/cover to the bright opaque ring's outer footprint
        (α≥160), then inset cover + clip by ``_OUTER_FILL_INSET`` so the photo
        sits under the bright rim; clip to hole ∪ bright rim so photo does not
        enter the soft glow. Full border including soft glow is still drawn on top.
      - ``circle_cutout`` — edge flood-fill keys charcoal-grey pad to transparent;
        no border is drawn (``border_name`` ignored / treated as none). Photo is
        aspect-fit into the display box.
    """
    size = max(16, int(display))
    fill = (fill_mode or "hole").strip().lower()
    circle_cutout = fill == "circle_cutout"
    # No glow / hilight frame for cutout portraits.
    if circle_cutout:
        border_name = None
    border_key = (border_name or "").strip()
    scale = float(border_scale)
    if scale == 1.0 and border_key == "CheckButtonHilight-Blue.PNG":
        scale = _HILIGHT_BLUE_SCALE
    outer = fill == "outer"

    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    border = QPixmap()
    framed, ox, oy, piece = QPixmap(), 0, 0, size
    if border_key:
        border_path = theme_file(border_key)
        border = QPixmap(str(border_path)) if border_path.is_file() else QPixmap()
        framed, ox, oy, piece = _prepare_border(border, size, scale=scale)

    photo_path = theme_file(photo_name)
    photo = QPixmap(str(photo_path)) if photo_path.is_file() else QPixmap()
    if circle_cutout and not photo.isNull():
        photo = _key_edge_grey(photo)

    if not photo.isNull() and piece > 0:
        clip = QPixmap()
        fill_box = QRect(0, 0, piece, piece)
        if circle_cutout:
            # Full display box; grey already keyed — no border hole clip.
            fill_box = QRect(0, 0, size, size)
            ox, oy, piece = 0, 0, size
        elif not border.isNull():
            soft = _alpha_bounds(border.toImage(), _SOFT_ALPHA_THR)
            hole_src = border.copy(soft) if soft.isValid() else border
            if outer:
                # Bright-ring outer footprint (not soft glow); clip to silhouette.
                clip = _outer_bright_mask(hole_src, piece)
                # 1px inward so cover/clip sit under the bright rim (no overhang).
                clip = _inset_disk_mask(clip, _OUTER_FILL_INSET)
            else:
                clip = _center_hole_mask(hole_src, piece)
            bounds = _alpha_bounds(clip.toImage(), 128)
            if bounds.isValid() and bounds.width() > 0 and bounds.height() > 0:
                # Outer: bounds already reflect the 1px-shrunk clip disk.
                fill_box = bounds

        fitted = _photo_for_hole(
            photo, fill_box.width(), fill_box.height(), crop_mode=crop_mode
        )

        layer = QPixmap(size, size)
        layer.fill(Qt.GlobalColor.transparent)
        lp = QPainter(layer)
        lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        # Center the fitted photo inside the fill box.
        dx = ox + fill_box.x() + (fill_box.width() - fitted.width()) // 2
        dy = oy + fill_box.y() + (fill_box.height() - fitted.height()) // 2
        lp.drawPixmap(dx, dy, fitted)
        if not clip.isNull():
            clip_full = QPixmap(size, size)
            clip_full.fill(Qt.GlobalColor.transparent)
            hp = QPainter(clip_full)
            hp.drawPixmap(ox, oy, clip)
            hp.end()
            lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            lp.drawPixmap(0, 0, clip_full)
        lp.end()
        p.drawPixmap(0, 0, layer)

    if not framed.isNull():
        p.drawPixmap(ox, oy, framed)

    p.end()
    return out


class ContributorPortrait(QWidget):
    """Compact framed portrait icon for the play-bar Contributors cluster."""

    def __init__(
        self,
        photo_name: str,
        parent=None,
        tooltip: str = "",
        *,
        border_name: str | None = _DEFAULT_BORDER,
        crop_mode: str = "contain",
        border_scale: float = 1.0,
        fill_mode: str = "hole",
        url: str = "",
        glow_ramp=None,
    ):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # Portrait plus a margin for its glow to fall off in. Qt clips painting
        # to the widget rect, so without the margin the halo ends on a square.
        # Scaled to the portrait rather than reusing the launch plate's numbers:
        # 52px of art does not want 12px of glow around it. Because the glow is
        # contained by the widget, adjacent portraits cannot bleed into each
        # other however bright they get.
        self._glow_ramp = glow_ramp
        self._glow_margin = _GLOW_MARGIN if glow_ramp else 0
        side = _DISPLAY + self._glow_margin * 2
        self.setFixedSize(side, side)
        self._url = (url or "").strip()
        self._tip_name = (tooltip or "").strip()
        self._name_tip: ContributorNameTip | None = None
        if self._url:
            apply_open_hand(self)
        self._pix = compose_contributor_portrait(
            photo_name,
            border_name=border_name,
            crop_mode=crop_mode,
            border_scale=border_scale,
            fill_mode=fill_mode,
        )

    def sizeHint(self) -> QSize:
        return QSize(_DISPLAY, _DISPLAY)

    def _ensure_name_tip(self) -> ContributorNameTip:
        if self._name_tip is None:
            self._name_tip = ContributorNameTip(self.window())
            # Sampled from this portrait's own art, so the label and the glow
            # cannot disagree and swapping the picture retints both.
            from ichalaunch.ui.widgets.gradient_label import sample_ramp_from_pixmap

            try:
                self._name_tip.set_ramp(sample_ramp_from_pixmap(self._pix))
            except Exception:  # noqa: BLE001 - a tooltip must never break a hover
                pass
            self._name_tip.set_name(self._tip_name)
            self._name_tip.destroyed.connect(self._clear_name_tip)
        return self._name_tip

    def _clear_name_tip(self, *_args) -> None:
        self._name_tip = None

    def _sync_glow_ticks(self, on: bool) -> None:
        """Subscribe to the shared phase only while hovered."""
        if not self._glow_ramp:
            return
        from ichalaunch.ui.widgets.gradient_label import lava_ticker

        if on:
            lava_ticker().subscribe(self)
        else:
            lava_ticker().unsubscribe(self)
        self.update()

    def enterEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._tip_name:
            self._ensure_name_tip().popup_above(self)
        super().enterEvent(event)
        self._sync_glow_ticks(True)

    def leaveEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._name_tip is not None:
            self._name_tip.dismiss()
        super().leaveEvent(event)
        self._sync_glow_ticks(False)

    def hideEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._name_tip is not None:
            self._name_tip.dismiss()
        super().hideEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._url
            and event.button() == Qt.MouseButton.LeftButton
        ):
            if discord_user_id_from_url(self._url):
                open_discord_user_profile(self._url)
            else:
                open_url_in_browser(self._url)
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        del event
        if self._pix.isNull():
            return
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            m = self._glow_margin
            if self._glow_ramp and self.underMouse() and self.isEnabled():
                # Same three layers as the launch plate, driven by the same shared
                # phase, differing only in the colour stops handed to them.
                from ichalaunch.ui.widgets.gradient_label import (
                    lava_flicker,
                    lava_rim_pixmap,
                    lava_ticker,
                    soft_halo,
                )

                deg = lava_ticker().phase
                peak = self._glow_ramp[len(self._glow_ramp) // 2][1]
                soft = soft_halo(self._pix, peak, _GLOW_HALO_PAD, _GLOW_HALO_BLUR)
                pad = _GLOW_HALO_PAD
                box = self.rect().adjusted(m - pad, m - pad, -(m - pad), -(m - pad))
                p.setOpacity(0.55 * lava_flicker(deg * 0.6))
                p.drawPixmap(box, soft)
                rim = lava_rim_pixmap(soft, deg, self._glow_ramp)
                p.setOpacity(0.70 * lava_flicker(deg))
                p.drawPixmap(box, rim)
                p.setOpacity(1.0)
            p.drawPixmap(m, m, self._pix)
        finally:
            p.end()
