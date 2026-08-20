"""Rotating talent-frame backgrounds for the HOME brand pane."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPixmap,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import QWidget

from ichalaunch.core.paths import theme_file

# Slow, tasteful rotation — hold then gentle crossfade (~2× prior pace).
_HOLD_MS = 11_000
_FADE_MS = 2_800
_BASE_OPACITY = 0.82
# Soft L/R + top falloff. Bottom stays hard so art still sits flush on the
# diamond strip (any bottom feather reads as a mid-page gap).
_EDGE_FEATHER = 0.04
_EDGE_FEATHER_TOP = 0.12
_EDGE_FEATHER_BOTTOM = 0.0
# Strip only fully-transparent padding (talent PNGs are square canvases with a
# clear bottom pad). Never luma/content-crop — that destroyed the widescreen frame.
_PAD_ALPHA_MIN = 1

_EXTERNAL_DIR = Path(r"F:\wow-ui-textures\TALENTFRAME")

TALENT_BG_NAMES: tuple[str, ...] = (
    "bg-warrior-protection.PNG",
    "bg-deathknight-blood.PNG",
    "bg-deathknight-frost.PNG",
    "bg-deathknight-unholy.PNG",
    "bg-druid-balance.PNG",
    "bg-druid-bear.PNG",
    "bg-druid-cat.PNG",
    "bg-druid-restoration.PNG",
    "bg-hunter-beastmaster.PNG",
    "bg-hunter-marksman.PNG",
    "bg-hunter-survival.PNG",
    "bg-mage-arcane.PNG",
    "bg-mage-fire.PNG",
    "bg-mage-frost.PNG",
    "bg-monk-battledancer.PNG",
    "bg-monk-brewmaster.PNG",
    "bg-monk-mistweaver.PNG",
    "bg-paladin-holy.PNG",
    "bg-paladin-protection.PNG",
    "bg-paladin-retribution.PNG",
    "bg-priest-discipline.PNG",
    "bg-priest-holy.PNG",
    "bg-priest-shadow.PNG",
    "bg-rogue-assassination.PNG",
    "bg-rogue-combat.PNG",
    "bg-rogue-subtlety.PNG",
    "bg-shaman-elemental.PNG",
    "bg-shaman-enhancement.PNG",
    "bg-shaman-restoration.PNG",
    "bg-warlock-affliction.PNG",
    "bg-warlock-demonology.PNG",
    "bg-warlock-destruction.PNG",
    "bg-warrior-arms.PNG",
    "bg-warrior-fury.PNG",
)


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _resolve_path(name: str) -> Path | None:
    bundled = theme_file("talent_bgs", name)
    if bundled.is_file():
        return bundled
    external = _EXTERNAL_DIR / name
    if external.is_file():
        return external
    return None


def _load_raw(name: str) -> QPixmap:
    path = _resolve_path(name)
    if path is None:
        return QPixmap()
    pm = QPixmap(str(path))
    return pm if not pm.isNull() else QPixmap()


def _transparent_pad_bounds(pm: QPixmap, alpha_min: int = _PAD_ALPHA_MIN) -> QRect:
    """Bounding rect of any non-transparent pixels — no luma / content trim."""
    if pm.isNull():
        return QRect()
    img = pm.toImage()
    w, h = img.width(), img.height()

    def row_has(y: int) -> bool:
        for x in range(w):
            if img.pixelColor(x, y).alpha() >= alpha_min:
                return True
        return False

    def col_has(x: int, y0: int, y1: int) -> bool:
        for y in range(y0, y1 + 1):
            if img.pixelColor(x, y).alpha() >= alpha_min:
                return True
        return False

    top = 0
    while top < h and not row_has(top):
        top += 1
    if top >= h:
        return QRect(0, 0, w, h)
    bottom = h - 1
    while bottom > top and not row_has(bottom):
        bottom -= 1
    left = 0
    while left < w and not col_has(left, top, bottom):
        left += 1
    right = w - 1
    while right > left and not col_has(right, top, bottom):
        right -= 1
    return QRect(left, top, right - left + 1, bottom - top + 1)


def _load_framed(name: str) -> QPixmap:
    """Full art with empty transparent canvas pad removed — keeps widescreen frame."""
    raw = _load_raw(name)
    if raw.isNull():
        return raw
    bounds = _transparent_pad_bounds(raw)
    if bounds.isEmpty() or bounds == QRect(0, 0, raw.width(), raw.height()):
        return raw
    framed = raw.copy(bounds)
    return framed if not framed.isNull() else raw


def _edge_mask(
    width: int,
    height: int,
    feather: float = _EDGE_FEATHER,
    feather_top: float = _EDGE_FEATHER_TOP,
    feather_bottom: float = _EDGE_FEATHER_BOTTOM,
) -> QPixmap:
    """Rect alpha mask: soft L/R + top; hard bottom for banner flush."""
    if width <= 0 or height <= 0:
        return QPixmap()
    base_w, base_h = 128, 128
    min_base = float(min(base_w, base_h))
    feather_px = max(1.0, min_base * max(0.02, min(0.45, feather))) if feather > 0 else 0.0
    if feather_top <= 0.0:
        feather_t_px = 0.0
    else:
        feather_t_px = max(1.0, min_base * max(0.01, min(0.45, feather_top)))
    if feather_bottom <= 0.0:
        feather_b_px = 0.0
    else:
        feather_b_px = max(1.0, min_base * max(0.01, min(0.45, feather_bottom)))
    img = QImage(base_w, base_h, QImage.Format.Format_ARGB32)
    last_x = base_w - 1
    last_y = base_h - 1
    for y in range(base_h):
        for x in range(base_w):
            dist_l = x
            dist_r = last_x - x
            dist_t = y
            dist_b = last_y - y
            a = 1.0
            for dist, fpx in (
                (dist_l, feather_px),
                (dist_r, feather_px),
                (dist_t, feather_t_px),
                (dist_b, feather_b_px),
            ):
                if fpx <= 0.0:
                    continue
                if dist <= 0:
                    a = 0.0
                    break
                if dist < fpx:
                    a *= _smoothstep(dist / fpx)
            img.setPixelColor(x, y, QColor(255, 255, 255, int(255 * a)))
    small = QPixmap.fromImage(img)
    if width == base_w and height == base_h:
        return small
    return small.scaled(
        width,
        height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


class TalentFrameBackground(QWidget):
    """Crossfading talent-frame texture; geometry is owned by HomePage."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")

        self._paths: list[str] = []
        self._cache: dict[str, QPixmap] = {}
        self._index = 0
        self._next_index = 0
        self._cur_opacity = _BASE_OPACITY
        self._nxt_opacity = 0.0
        self._mask: QPixmap = QPixmap()
        self._mask_w = 0
        self._mask_h = 0
        self._fade: QParallelAnimationGroup | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)

        self._discover()

    def _discover(self) -> None:
        names: list[str] = []
        for name in TALENT_BG_NAMES:
            if _resolve_path(name) is not None:
                names.append(name)
        self._paths = names

    def _pixmap(self, name: str) -> QPixmap:
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        pm = _load_framed(name)
        if not pm.isNull():
            self._cache[name] = pm
        return pm

    def source_size(self) -> tuple[int, int]:
        """Native (src_w, src_h) after transparent-pad trim (widescreen frame)."""
        if not self._paths:
            return (1, 1)
        pm = self._pixmap(self._paths[self._index])
        if pm.isNull() or pm.width() <= 0 or pm.height() <= 0:
            return (1, 1)
        return (pm.width(), pm.height())

    def source_aspect(self) -> float:
        """src_w / src_h — layout uses height = width / aspect."""
        sw, sh = self.source_size()
        return float(sw) / float(sh)

    def getCurOpacity(self) -> float:
        return self._cur_opacity

    def setCurOpacity(self, value: float) -> None:
        self._cur_opacity = max(0.0, min(1.0, float(value)))
        self.update()

    curOpacity = Property(float, getCurOpacity, setCurOpacity)

    def getNxtOpacity(self) -> float:
        return self._nxt_opacity

    def setNxtOpacity(self, value: float) -> None:
        self._nxt_opacity = max(0.0, min(1.0, float(value)))
        self.update()

    nxtOpacity = Property(float, getNxtOpacity, setNxtOpacity)

    def set_frame(self, x: int, y: int, w: int, h: int) -> None:
        """Absolute frame in parent coords (caller sizes from source aspect)."""
        w = max(0, int(w))
        h = max(0, int(h))
        if w <= 0 or h <= 0 or not self._paths:
            self.hide()
            return
        self.setGeometry(int(x), int(y), w, h)
        self._ensure_mask(w, h)
        self.show()

    def _ensure_mask(self, width: int, height: int) -> None:
        if (
            width == self._mask_w
            and height == self._mask_h
            and not self._mask.isNull()
        ):
            return
        self._mask = _edge_mask(width, height)
        self._mask_w = width
        self._mask_h = height

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._paths and not self._timer.isActive():
            self._pixmap(self._paths[self._index])
            if len(self._paths) > 1:
                self._pixmap(self._paths[(self._index + 1) % len(self._paths)])
            self._timer.start(_HOLD_MS)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._ensure_mask(self.width(), self.height())

    def paintEvent(self, event) -> None:  # noqa: ANN001
        if not self._paths:
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        self._ensure_mask(w, h)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        def _draw(name: str, opacity: float) -> None:
            if opacity <= 0.01:
                return
            src = self._pixmap(name)
            if src.isNull():
                return
            # Widget is aspect-correct — KeepAspectRatio shows the full frame,
            # no Expanding crop / overscale.
            scaled = src.scaled(
                w,
                h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (w - scaled.width()) // 2
            y = (h - scaled.height()) // 2
            layer = QPixmap(w, h)
            layer.fill(Qt.GlobalColor.transparent)
            lp = QPainter(layer)
            lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            lp.drawPixmap(x, y, scaled)
            if not self._mask.isNull():
                lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                lp.drawPixmap(0, 0, self._mask)
            lp.end()
            painter.setOpacity(opacity)
            painter.drawPixmap(0, 0, layer)

        _draw(self._paths[self._index], self._cur_opacity)
        if self._nxt_opacity > 0.01:
            _draw(self._paths[self._next_index], self._nxt_opacity)
        painter.end()

    def _advance(self) -> None:
        if len(self._paths) < 2:
            return
        if self._fade is not None and self._fade.state() == QParallelAnimationGroup.State.Running:
            return

        self._next_index = (self._index + 1) % len(self._paths)
        self._pixmap(self._paths[self._next_index])
        self._pixmap(self._paths[(self._next_index + 1) % len(self._paths)])

        fade_out = QPropertyAnimation(self, b"curOpacity")
        fade_out.setDuration(_FADE_MS)
        fade_out.setStartValue(self._cur_opacity)
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InOutQuad)

        fade_in = QPropertyAnimation(self, b"nxtOpacity")
        fade_in.setDuration(_FADE_MS)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(_BASE_OPACITY)
        fade_in.setEasingCurve(QEasingCurve.Type.InOutQuad)

        group = QParallelAnimationGroup(self)
        group.addAnimation(fade_out)
        group.addAnimation(fade_in)

        def _done() -> None:
            self._index = self._next_index
            self._cur_opacity = _BASE_OPACITY
            self._nxt_opacity = 0.0
            self._fade = None
            self.update()
            self._timer.start(_HOLD_MS)

        group.finished.connect(_done)
        self._fade = group
        group.start()
