"""WoW Glue-Panel buttons (Up/Down art) for main toolbar actions."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QPushButton, QSizePolicy

from ichalaunch.core.paths import theme_file
from ichalaunch.ui.widgets.cursors import apply_open_hand
from ichalaunch.ui.theme_fonts import chrome_family

_UP_NAME = "Glue-Panel-Button-Up-v2.PNG"
_DOWN_NAME = "Glue-Panel-Button-Down-v2.PNG"
_UP_EXTERNAL = Path(r"F:\wow-ui-textures\GLUES\COMMON\Glue-Panel-Button-Up-v2.PNG")
_DOWN_EXTERNAL = Path(r"F:\wow-ui-textures\GLUES\COMMON\Glue-Panel-Button-Down-v2.PNG")
_BIGGER_UP_NAME = "UI-Panel-BiggerButton-Up.PNG"
_BIGGER_DOWN_NAME = "UI-Panel-BiggerButton-Down.PNG"
_BIGGER_UP_EXTERNAL = Path(r"F:\wow-ui-textures\Buttons\UI-Panel-BiggerButton-Up.PNG")
_BIGGER_DOWN_EXTERNAL = Path(r"F:\wow-ui-textures\Buttons\UI-Panel-BiggerButton-Down.PNG")
_CHROME_PANEL = "panel"
_CHROME_BIGGER = "bigger"

# Shared toolbar size so Rescan / Check Updates / Update All / + Git Repo match.
GLUE_BTN_W = 128
GLUE_BTN_H = 34
# Compact size for Addons / Client row actions (Install, Update, Remove, …).
GLUE_ROW_W = 100
GLUE_ROW_H = 28
GLUE_ROW_MENU_W = 28

_GOLD = QColor("#F1C22D")
_GOLD_SOFT = QColor("#E8C878")
_TEXT = QColor("#ece3d2")
_TEXT_DIM = QColor("#8f8574")

# HSV targets for the red *fill* only (border greys are left alone).
# These were purple, hues 265 to 275, and purple appears nowhere on
# ravencraft.io: the site frames everything in brown and bronze stonework with
# gold headings. The hues now sit on the site's own gold, which measures hue 37
# for the bronze and 45 for the heading gold. Saturation and value scales are
# unchanged, so the bevel and hover relationships that were tuned against the
# original red art still hold.
_FILL_STANDARD = (36, 100, 1.05)   # hue, sat, value scale
_FILL_PRIMARY = (38, 175, 1.25)
_FILL_PRIMARY_BRIGHT = (42, 200, 1.35)

# Square UPDATE plate: keep L/R metal caps, compress only the middle fill.
# Caps are the trimmed metal (~16–20px), not the padded 32px of the 512 source
# (that left transparent side gutters and a tall visible plate).
_LAUNCH_SQUARE_SIDE = 56
_LAUNCH_SQUARE_CAP = 20

# ContentPanel floor / _FLOOR_BASE — tab plates tint toward this, not purple.
GLUE_FLOOR_TINT = QColor("#1b1512")
# Typical luminance of the glue-plate red fill (center ~107,0,0) so idle
# fill maps onto GLUE_FLOOR_TINT while metal bevels keep relative contrast.
_FLOOR_REF_LUM = 32.0
_FLOOR_SHADE_LIFT = {
    "idle": 1.00,
    "hover": 1.22,
    "selected": 1.45,
}

_RAW: dict[str, QImage] = {}
_RECOLOR: dict[tuple[str, str, bool], QPixmap] = {}
_LAUNCH: dict[tuple[bool, bool, bool, int], QPixmap] = {}
_ROW_SQUARE: dict[tuple[bool, bool, str, int], QPixmap] = {}
_FLOOR_CHROME: dict[tuple[bool, str], QPixmap] = {}
_OPEN_GIT_ICON: dict[tuple[bool, bool, int], QPixmap] = {}
_ROW_PLATE_METRICS: tuple[int, int] | None = None
_DIALOG_PLATE_METRICS: tuple[int, int] | None = None


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


def _chrome_sources(
    *,
    pressed: bool,
    variant: str,
) -> tuple[str, Path]:
    if variant == _CHROME_BIGGER:
        if pressed:
            return _BIGGER_DOWN_NAME, _BIGGER_DOWN_EXTERNAL
        return _BIGGER_UP_NAME, _BIGGER_UP_EXTERNAL
    if pressed:
        return _DOWN_NAME, _DOWN_EXTERNAL
    return _UP_NAME, _UP_EXTERNAL


def glue_chrome_pixmap(
    *,
    pressed: bool = False,
    role: str = "standard",
    disabled: bool = False,
    variant: str = _CHROME_PANEL,
) -> QPixmap:
    """Recolored Glue-Panel / BiggerButton Up/Down art (red fill → purple)."""
    if role not in ("standard", "primary", "primary_bright"):
        role = "standard"
    name, ext = _chrome_sources(pressed=pressed, variant=variant)
    return _colored_pm(name, ext, role, disabled)


def _is_purple_fill(c: QColor) -> bool:
    """True for recolored glue-panel / bigger-button fill (not metal border)."""
    if c.alpha() < 16:
        return False
    h, s, v = c.hue(), c.saturation(), c.value()
    if h < 0:
        return False
    return (240 <= h <= 295) and s >= 70 and v >= 28


def _purple_fill_rect(pm: QPixmap) -> QRect:
    if pm.isNull():
        return QRect()
    img = pm.toImage()
    w, h = img.width(), img.height()
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if _is_purple_fill(QColor.fromRgba(img.pixel(x, y))):
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


def action_plate_metrics(width: int, height: int) -> tuple[int, int]:
    """Purple fill height and top inset for glue chrome at *width*×*height*."""
    pm = glue_chrome_pixmap(variant=_CHROME_PANEL)
    if pm.isNull() or width <= 0 or height <= 0:
        return max(1, height), 0
    scaled = pm.scaled(
        int(width),
        int(height),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    fill = _purple_fill_rect(scaled)
    if fill.isValid():
        return fill.height(), fill.y()
    bounds = _opaque_rect(scaled.toImage())
    if bounds.isValid():
        return bounds.height(), bounds.y()
    return max(1, height), 0


def row_action_plate_metrics() -> tuple[int, int]:
    """Match Install / row glue buttons (``GLUE_ROW_W`` × ``GLUE_ROW_H``)."""
    global _ROW_PLATE_METRICS
    if _ROW_PLATE_METRICS is None:
        _ROW_PLATE_METRICS = action_plate_metrics(GLUE_ROW_W, GLUE_ROW_H)
    return _ROW_PLATE_METRICS


def dialog_action_plate_metrics() -> tuple[int, int]:
    """Match dialog glue buttons (``GLUE_BTN_W`` × ``GLUE_BTN_H``)."""
    global _DIALOG_PLATE_METRICS
    if _DIALOG_PLATE_METRICS is None:
        _DIALOG_PLATE_METRICS = action_plate_metrics(GLUE_BTN_W, GLUE_BTN_H)
    return _DIALOG_PLATE_METRICS


def open_git_icon_pixmap(
    *,
    pressed: bool = False,
    disabled: bool = False,
    side: int = GLUE_ROW_H,
) -> QPixmap:
    """Purple-tinted BiggerButton art cropped and scaled to fit a *side*×*side* plate."""
    key = (bool(pressed), bool(disabled), int(side))
    hit = _OPEN_GIT_ICON.get(key)
    if hit is not None:
        return hit
    raw = glue_chrome_pixmap(
        pressed=pressed,
        disabled=disabled,
        variant=_CHROME_BIGGER,
        role="standard",
    )
    if raw.isNull():
        _OPEN_GIT_ICON[key] = QPixmap()
        return _OPEN_GIT_ICON[key]
    target = max(1, int(side))
    bounds = _opaque_rect(raw.toImage())
    cropped = raw.copy(bounds) if bounds.isValid() else raw
    if cropped.height() <= 0 or cropped.width() <= 0:
        _OPEN_GIT_ICON[key] = QPixmap()
        return _OPEN_GIT_ICON[key]
    scale = target / float(max(cropped.width(), cropped.height()))
    out_w = max(1, int(round(cropped.width() * scale)))
    out_h = max(1, int(round(cropped.height() * scale)))
    pm = cropped.scaled(
        out_w,
        out_h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    _OPEN_GIT_ICON[key] = pm
    return pm


def _luminance(c: QColor) -> float:
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


def tint_image_toward_color(
    img: QImage,
    target: QColor,
    *,
    lift: float = 1.0,
    ref_lum: float = _FLOOR_REF_LUM,
) -> QImage:
    """Tint every opaque texel in *img* toward *target*, keeping relative contrast."""
    if img.isNull():
        return QImage()
    if img.format() != QImage.Format.Format_ARGB32:
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
    out = img.copy()
    tr, tg, tb = target.red(), target.green(), target.blue()
    for y in range(out.height()):
        for x in range(out.width()):
            c = QColor.fromRgba(out.pixel(x, y))
            a = c.alpha()
            if a < 8:
                continue
            factor = (_luminance(c) / ref_lum) * lift
            out.setPixel(
                x,
                y,
                QColor(
                    max(0, min(255, int(round(tr * factor)))),
                    max(0, min(255, int(round(tg * factor)))),
                    max(0, min(255, int(round(tb * factor)))),
                    a,
                ).rgba(),
            )
    return out


def tint_pixmap_toward_color(
    pm: QPixmap,
    target: QColor,
    *,
    lift: float = 1.0,
    ref_lum: float = _FLOOR_REF_LUM,
) -> QPixmap:
    """Tint every opaque texel in *pm* toward *target*, keeping relative contrast."""
    if pm.isNull():
        return QPixmap()
    out = tint_image_toward_color(pm.toImage(), target, lift=lift, ref_lum=ref_lum)
    return QPixmap.fromImage(out) if not out.isNull() else QPixmap()


def _tint_art_toward_floor(src: QImage, target: QColor, lift: float) -> QPixmap:
    """Pixel-filter every opaque texel toward ``target``, keeping bevel contrast.

    Fill luminance (~32) maps onto the target; brighter metal stays relatively
    lighter so the plate still reads as Glue-Panel chrome, not a flat swatch.
    """
    if src.isNull():
        return QPixmap()
    out = tint_image_toward_color(src, target, lift=lift)
    return QPixmap.fromImage(out) if not out.isNull() else QPixmap()


def glue_floor_chrome_pixmap(*, pressed: bool = False, shade: str = "idle") -> QPixmap:
    """Standard Glue-Panel Up/Down art tinted to the ContentPanel floor color."""
    if shade not in _FLOOR_SHADE_LIFT:
        shade = "idle"
    key = (bool(pressed), shade)
    hit = _FLOOR_CHROME.get(key)
    if hit is not None:
        return hit
    name, ext = (_DOWN_NAME, _DOWN_EXTERNAL) if pressed else (_UP_NAME, _UP_EXTERNAL)
    pm = _tint_art_toward_floor(
        _load_image(name, ext),
        GLUE_FLOOR_TINT,
        _FLOOR_SHADE_LIFT[shade],
    )
    if not pm.isNull():
        bounds = _opaque_rect(pm.toImage())
        if bounds.isValid() and bounds != pm.rect():
            pm = pm.copy(bounds)
    _FLOOR_CHROME[key] = pm
    return pm


def _embellish_launch_fill(src: QImage) -> QPixmap:
    """Red fill → primary purple, then PLAY-style bottom glow + a soft gold underline.

    Same pixel walk as toolbar recolor: metal borders stay untouched. Fill
    value is darkened at the top and lifted toward the bottom so the taller
    PLAY / REGISTER / UPDATE plates match the old launch chrome gradient.
    """
    if src.isNull():
        return QPixmap()
    out = src.copy()
    w, h = out.width(), out.height()
    hue, sat, base_scale = _FILL_PRIMARY

    top, bottom = h, -1
    for y in range(h):
        for x in range(w):
            if _is_red_fill(QColor.fromRgba(out.pixel(x, y))):
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y
    if bottom < top:
        return QPixmap.fromImage(out)

    span = max(1, bottom - top)
    for y in range(h):
        t = (y - top) / span
        t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
        grad = 0.30 + 1.10 * (t ** 1.25)
        for x in range(w):
            c = QColor.fromRgba(out.pixel(x, y))
            if not _is_red_fill(c):
                continue
            v = max(0, min(255, int(round(c.value() * base_scale * grad))))
            out.setPixel(x, y, QColor.fromHsv(hue, sat, v, c.alpha()).rgba())

    # Soft gold underline — one muted row blended into the fill (not a hard bar).
    uy = bottom - 3
    if 0 <= uy < h:
        gold = QColor(196, 158, 68)  # #C49E44, antique gold (not #F1C22D)
        mix = 0.58
        keep = 1.0 - mix
        for x in range(w):
            c = QColor.fromRgba(out.pixel(x, uy))
            if c.alpha() < 16:
                continue
            hh = c.hue()
            if not (240 <= hh <= 300 and c.saturation() >= 60):
                continue
            out.setPixel(
                x,
                uy,
                QColor(
                    int(round(c.red() * keep + gold.red() * mix)),
                    int(round(c.green() * keep + gold.green() * mix)),
                    int(round(c.blue() * keep + gold.blue() * mix)),
                    c.alpha(),
                ).rgba(),
            )
    return QPixmap.fromImage(out)


def _opaque_rect(img: QImage, alpha_min: int = 20) -> QRect:
    if img.isNull():
        return QRect()
    w, h = img.width(), img.height()
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if QColor.fromRgba(img.pixel(x, y)).alpha() >= alpha_min:
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


def _squish_plate_to_square(pm: QPixmap, side: int) -> QPixmap:
    """3-slice the wide glue plate into a *visible* square.

    Trim transparent gutters first (the 512 source is inset ~10px on the
    sides). Then keep L/R metal caps and compress the middle. Finally scale
    the opaque result into ``side``×``side`` so the plate is not a tall
    rectangle sitting inside a square pixmap.
    """
    if pm.isNull() or side <= 0:
        return pm
    bounds = _opaque_rect(pm.toImage())
    if bounds.isValid() and bounds != pm.rect():
        pm = pm.copy(bounds)
    src_w, src_h = pm.width(), pm.height()
    cap = min(_LAUNCH_SQUARE_CAP, max(1, src_w // 4))
    dest_cap = max(1, int(round(cap * (side / max(1, src_h)))))
    dest_cap = min(dest_cap, max(1, side // 4))
    mid = side - 2 * dest_cap
    sliced = QPixmap(side, side)
    sliced.fill(Qt.GlobalColor.transparent)
    p = QPainter(sliced)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawPixmap(QRect(0, 0, dest_cap, side), pm, QRect(0, 0, cap, src_h))
    p.drawPixmap(
        QRect(side - dest_cap, 0, dest_cap, side),
        pm,
        QRect(src_w - cap, 0, cap, src_h),
    )
    if mid > 0:
        p.drawPixmap(
            QRect(dest_cap, 0, mid, side),
            pm,
            QRect(cap, 0, src_w - 2 * cap, src_h),
        )
    p.end()
    vis = _opaque_rect(sliced.toImage())
    if not vis.isValid() or vis == QRect(0, 0, side, side):
        return sliced
    filled = QPixmap(side, side)
    filled.fill(Qt.GlobalColor.transparent)
    p = QPainter(filled)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawPixmap(QRect(0, 0, side, side), sliced, vis)
    p.end()
    return filled


def glue_row_square_chrome(
    *,
    pressed: bool = False,
    role: str = "standard",
    disabled: bool = False,
    side: int = GLUE_ROW_H,
) -> QPixmap:
    """Square glue-panel chrome for compact addon-row icon buttons (Remove, …)."""
    if role not in ("standard", "primary", "primary_bright"):
        role = "standard"
    key = (pressed, disabled, role, int(side))
    hit = _ROW_SQUARE.get(key)
    if hit is not None:
        return hit
    pm = glue_chrome_pixmap(pressed=pressed, role=role, disabled=disabled)
    if not pm.isNull():
        pm = _squish_plate_to_square(pm, int(side) if side > 0 else GLUE_ROW_H)
    _ROW_SQUARE[key] = pm
    return pm


def launch_glue_chrome(
    *,
    pressed: bool = False,
    disabled: bool = False,
    square: bool = False,
    side: int = _LAUNCH_SQUARE_SIDE,
) -> QPixmap:
    """PLAY / REGISTER / UPDATE chrome: purple glue-panel + gradient + soft gold line.

    ``square=True`` 3-slices the full Up/Down art into a square (left + right
    metal caps kept, middle compressed). Not a center crop.
    """
    key = (pressed, disabled, square, int(side) if square else 0)
    hit = _LAUNCH.get(key)
    if hit is not None:
        return hit
    name, ext = (_DOWN_NAME, _DOWN_EXTERNAL) if pressed else (_UP_NAME, _UP_EXTERNAL)
    pm = _embellish_launch_fill(_load_image(name, ext))
    if square and not pm.isNull():
        pm = _squish_plate_to_square(pm, int(side) if side > 0 else _LAUNCH_SQUARE_SIDE)
    if disabled and not pm.isNull():
        dim = QPixmap(pm.size())
        dim.fill(Qt.GlobalColor.transparent)
        p = QPainter(dim)
        p.drawPixmap(0, 0, pm)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        p.fillRect(dim.rect(), QColor(30, 28, 34, 110))
        p.end()
        pm = dim
    _LAUNCH[key] = pm
    return pm


_GLOW_NAME = "CheckButtonGlow.PNG"
_GLOW_EXTERNAL = Path(r"F:\\wow-ui-textures\\Buttons") / _GLOW_NAME
_GLOW_PAD_ALPHA = 8
_GLOW_BY_PLATE: dict[tuple[int, int], QPixmap] = {}


def _glow_alpha_bounds(img: QImage, min_alpha: int) -> QRect:
    w, h = img.width(), img.height()
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if QColor.fromRgba(img.pixel(x, y)).alpha() >= min_alpha:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if max_x < min_x:
        return QRect()
    return QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def _glow_inner_hole_width(img: QImage) -> int:
    cy = img.height() // 2
    w = img.width()
    left_ring = None
    for x in range(w):
        if QColor.fromRgba(img.pixel(x, cy)).alpha() >= 64:
            left_ring = x
            break
    if left_ring is None:
        return 0
    hole_start = None
    for x in range(left_ring + 1, w):
        if QColor.fromRgba(img.pixel(x, cy)).alpha() < 8:
            hole_start = x
            break
    if hole_start is None:
        return 0
    hole_end = hole_start
    for x in range(hole_start, w):
        if QColor.fromRgba(img.pixel(x, cy)).alpha() >= 64:
            break
        hole_end = x
    return max(0, hole_end - hole_start + 1)


def check_button_glow_for_plate(plate_w: int, plate_h: int) -> QPixmap:
    """Pad-trimmed CheckButtonGlow scaled so the hole tracks plate W and H.

    X and Y are scaled independently so a rectangular plate gets even L/R and
    T/B insets (no detached side aura from a width-only +10 stretch).
    """
    key = (max(1, int(plate_w)), max(1, int(plate_h)))
    hit = _GLOW_BY_PLATE.get(key)
    if hit is not None:
        return hit
    path = theme_file(_GLOW_NAME)
    if not path.is_file():
        path = _GLOW_EXTERNAL
    if not path.is_file():
        pm = QPixmap()
        _GLOW_BY_PLATE[key] = pm
        return pm
    src = QPixmap(str(path))
    if src.isNull():
        _GLOW_BY_PLATE[key] = src
        return src
    img = src.toImage()
    pad = _glow_alpha_bounds(img, _GLOW_PAD_ALPHA)
    if pad.isValid() and pad != src.rect():
        src = src.copy(pad)
        img = src.toImage()
    hole = _glow_inner_hole_width(img) or 32
    target_w, target_h = key
    # Match hole to plate on both axes (square glow art → rectangular plate).
    dest_w = max(target_w + 12, int(round(src.width() * (target_w / hole))))
    dest_h = max(target_h + 12, int(round(src.height() * (target_h / hole))))
    pm = src.scaled(
        dest_w,
        dest_h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    _GLOW_BY_PLATE[key] = pm
    return pm



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
        glowing: bool = False,
        chrome_variant: str = _CHROME_PANEL,
    ):
        super().__init__(text, parent)
        assert role in ("standard", "primary")
        assert chrome_variant in (_CHROME_PANEL, _CHROME_BIGGER)
        self._role = role
        self._chrome_variant = chrome_variant
        self._pulse = False
        self._chrome_w = int(width)
        self._chrome_h = int(height)
        self._glowing = bool(glowing)
        self._glow_pm = QPixmap()
        self._glow_pulse = 0.0
        self._glow_timer: QTimer | None = None
        self.setObjectName("GluePanelButton")
        apply_open_hand(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if self._glowing:
            self._glow_pm = check_button_glow_for_plate(self._chrome_w, self._chrome_h)
            gw = self._glow_pm.width() if not self._glow_pm.isNull() else self._chrome_w + 16
            gh = self._glow_pm.height() if not self._glow_pm.isNull() else self._chrome_h + 16
            self.setFixedSize(max(self._chrome_w, gw), max(self._chrome_h, gh))
            self._glow_timer = QTimer(self)
            self._glow_timer.setInterval(40)
            self._glow_timer.timeout.connect(self._tick_glow_pulse)
            self._glow_timer.start()
        else:
            self.setFixedSize(self._chrome_w, self._chrome_h)
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
        for pressed in (False, True):
            name, ext = _chrome_sources(pressed=pressed, variant=self._chrome_variant)
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

    def _chrome_rect(self) -> QRect:
        if not getattr(self, "_glowing", False):
            return self.rect()
        return QRect(
            (self.width() - self._chrome_w) // 2,
            (self.height() - self._chrome_h) // 2,
            self._chrome_w,
            self._chrome_h,
        )

    def _tick_glow_pulse(self) -> None:
        self._glow_pulse = (self._glow_pulse + 0.10) % (2 * math.pi)
        self.update()

    def _paint_update_glow(self, painter: QPainter) -> None:
        if self._glow_pm.isNull() or not self.isEnabled():
            return
        wave = 0.5 + 0.5 * math.sin(self._glow_pulse)
        if self.underMouse() or self.isDown():
            opacity = 0.95
        else:
            opacity = 0.40 + 0.55 * wave
        painter.setOpacity(opacity)
        dest = self.rect()
        glow = self._glow_pm
        if glow.width() != dest.width() or glow.height() != dest.height():
            x = dest.center().x() - glow.width() // 2
            y = dest.center().y() - glow.height() // 2
            painter.drawPixmap(x, y, glow)
        else:
            painter.drawPixmap(dest, glow)
        painter.setOpacity(1.0)


    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        if getattr(self, "_glowing", False) and not self._glow_pm.isNull():
            self._paint_update_glow(painter)
        rect = self._chrome_rect()
        name, ext = _chrome_sources(
            pressed=self.isDown(),
            variant=self._chrome_variant,
        )
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
        fill = QColor("#6b4a1e") if self._role == "primary" else QColor("#2e2820")
        painter.setPen(QColor("#94836a"))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)

    def _paint_label(self, painter: QPainter, rect: QRect) -> None:
        text = self.text() or ""
        font = QFont(self.font())
        font.setFamily(chrome_family())
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
