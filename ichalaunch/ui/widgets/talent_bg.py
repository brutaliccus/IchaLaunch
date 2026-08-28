"""Rotating official RavenCraft artworks for the HOME brand pane."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QMetaObject,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    Signal,
    Slot,
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

from ichalaunch.core.logging_setup import log
from ichalaunch.core.paths import theme_file
from ichalaunch.ui.home_art import (
    fetch_missing_images,
    load_home_art,
    normalize_slide,
    refresh_home_art,
    resolve_image_path,
    resolved_slides,
)

# Slow, tasteful rotation — hold then gentle crossfade (~2× prior pace).
_HOLD_MS = 11_000
_FADE_MS = 2_800
_BASE_OPACITY = 0.82
# Soft L/R + top falloff. Bottom stays hard so art still sits flush on the
# diamond strip (any bottom feather reads as a mid-page gap).
_EDGE_FEATHER = 0.04
_EDGE_FEATHER_TOP = 0.26
_EDGE_FEATHER_BOTTOM = 0.0
# Strip only fully-transparent padding (legacy talent PNGs were square canvases).
_PAD_ALPHA_MIN = 1

_ART_DIR = "official_artworks"
_LEGACY_DIR = "talent_bgs"
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_EXTERNAL_DIR = Path(r"F:\wow-ui-textures\TALENTFRAME")

# Fallback list if discovery finds nothing (legacy talent frames).
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


def _list_dir_images(folder: Path) -> list[str]:
    if not folder.is_dir():
        return []
    return sorted(
        p.name
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
    )


def _plain_slide(name: str) -> dict[str, Any] | None:
    return normalize_slide({"id": Path(name).stem, "image": name})


def _resolve_path(name: str) -> Path | None:
    cached_or_bundled = resolve_image_path(name)
    if cached_or_bundled is not None:
        return cached_or_bundled
    root = theme_file(name)
    if root.is_file():
        return root
    for folder in (_ART_DIR, _LEGACY_DIR):
        bundled = theme_file(folder, name)
        if bundled.is_file():
            return bundled
    external = _EXTERNAL_DIR / name
    if external.is_file():
        return external
    return None


def _hold_ms_for(slide: dict[str, Any]) -> int:
    return max(1, int(_HOLD_MS * float(slide.get("hold") or 1.0)))


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
    # Opaque JPEG/PNG artworks: skip expensive pad scan.
    if name.lower().endswith((".jpg", ".jpeg")):
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
    """Crossfading official artwork; geometry is owned by HomePage."""

    frame_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")

        self._slides: list[dict[str, Any]] = []
        self._cache: dict[str, QPixmap] = {}
        self._overlay_cache: dict[str, QPixmap] = {}
        self._index = 0
        self._next_index = 0
        self._cur_opacity = _BASE_OPACITY
        self._nxt_opacity = 0.0
        self._mask: QPixmap = QPixmap()
        self._mask_w = 0
        self._mask_h = 0
        self._fade: QParallelAnimationGroup | None = None
        self._refresh_started = False

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)
        self._discover()
        QTimer.singleShot(0, self._kick_home_art_refresh)

    def _discover(self) -> None:
        # Local manifest only (memory / appdata / bundled). Network is later.
        slides = list(resolved_slides(load_home_art()))
        has_rest = any(
            str(s.get("fit") or "") != "width" and not s.get("frame") for s in slides
        )
        # Empty official gallery → same talent-frame fallback as before.
        if not has_rest:
            names = _list_dir_images(theme_file(_ART_DIR))
            if not names:
                names = [n for n in TALENT_BG_NAMES if _resolve_path(n) is not None]
            used = {str(s.get("image") or "").lower() for s in slides}
            for name in names:
                if name.lower() in used:
                    continue
                slide = _plain_slide(name)
                if slide is not None and _resolve_path(name) is not None:
                    slides.append(slide)
        self._slides = slides

    def _kick_home_art_refresh(self) -> None:
        if self._refresh_started:
            return
        self._refresh_started = True

        widget = self

        def work() -> None:
            try:
                refresh_home_art()
                fetch_missing_images()
            except Exception as exc:  # noqa: BLE001
                log.debug("Home art refresh skipped: %s", exc)
            QMetaObject.invokeMethod(
                widget, "_on_art_ready", Qt.ConnectionType.QueuedConnection
            )

        threading.Thread(
            target=work, daemon=True, name="home-art-refresh"
        ).start()

    @Slot()
    def _on_art_ready(self) -> None:
        prev = [str(s.get("id") or "") for s in self._slides]
        self._discover()
        if [str(s.get("id") or "") for s in self._slides] == prev:
            return
        self._cache.clear()
        self._overlay_cache.clear()
        if self._index >= len(self._slides):
            self._index = 0
        self.update()
        self.frame_changed.emit()

    def _slide_at(self, index: int) -> dict[str, Any] | None:
        if 0 <= index < len(self._slides):
            return self._slides[index]
        return None

    def _name_at(self, index: int) -> str:
        slide = self._slide_at(index)
        return str(slide.get("image") or "") if slide else ""

    def _pixmap(self, name: str) -> QPixmap:
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        pm = _load_framed(name)
        if not pm.isNull():
            self._cache[name] = pm
        return pm

    def _frame_overlay(self, slide: dict[str, Any]) -> QPixmap:
        overlay_name = str(slide.get("frame") or "")
        if not overlay_name:
            return QPixmap()
        cached = self._overlay_cache.get(overlay_name)
        if cached is not None:
            return cached
        pm = _load_raw(overlay_name)
        if not pm.isNull():
            self._overlay_cache[overlay_name] = pm
        return pm

    def source_size(self) -> tuple[int, int]:
        """Native (src_w, src_h) after transparent-pad trim (widescreen frame)."""
        slide = self._slide_at(self._index)
        if slide is None:
            return (1, 1)
        pm = self._pixmap(str(slide.get("image") or ""))
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
        if w <= 0 or h <= 0 or not self._slides:
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
        if self._slides and not self._timer.isActive():
            first = self._slide_at(self._index)
            if first is not None:
                self._pixmap(str(first.get("image") or ""))
            if len(self._slides) > 1:
                nxt = self._slide_at((self._index + 1) % len(self._slides))
                if nxt is not None:
                    self._pixmap(str(nxt.get("image") or ""))
            self._timer.start(_hold_ms_for(first or {"hold": 1.0}))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._ensure_mask(self.width(), self.height())

    def paintEvent(self, event) -> None:  # noqa: ANN001
        if not self._slides:
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        self._ensure_mask(w, h)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        def _draw(slide: dict[str, Any] | None, opacity: float) -> None:
            if slide is None or opacity <= 0.01:
                return
            name = str(slide.get("image") or "")
            src = self._pixmap(name)
            if src.isNull():
                return
            shrink_w = int(slide.get("shrink_w") or 0)
            nudge_x = int(slide.get("nudge_x") or 0)
            nudge_y = int(slide.get("nudge_y") or 0)
            if str(slide.get("fit") or "") == "width":
                # Full width, no L/R crop — honor unusual AR; centre the rest.
                #
                # A width-fit slide is wider in aspect than the brand rect, so
                # scaling it to the width always leaves a vertical remainder:
                # the featured 2:1 slide paints 542 tall in a 745 tall column.
                # Pinning to the banner banked all 203px of that above the
                # frame, which reads as a picture that has slipped down its
                # wall. Splitting the remainder is the only option that does
                # not crop: cover fills the rect but takes ~14% off each side,
                # and the featured slide carries its caption out there.
                dest_w = max(1, w - shrink_w) if shrink_w else w
                scaled = src.scaledToWidth(
                    dest_w, Qt.TransformationMode.SmoothTransformation
                )
                x = (w - scaled.width()) // 2
                y = max(0, (h - scaled.height()) // 2)
            else:
                # Cover the brand rect (center crop). Widget itself is
                # bottom-tucked to the nav banner.
                scaled = src.scaled(
                    w,
                    h,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = (w - scaled.width()) // 2
                y = (h - scaled.height()) // 2
            x += nudge_x
            y += nudge_y
            layer = QPixmap(w, h)
            layer.fill(Qt.GlobalColor.transparent)
            lp = QPainter(layer)
            lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            lp.drawPixmap(x, y, scaled)
            if not self._mask.isNull():
                lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                lp.drawPixmap(0, 0, self._mask)
            overlay = self._frame_overlay(slide)
            if not overlay.isNull() and scaled.width() > 0 and scaled.height() > 0:
                # Full-bleed border with transparent center — stretch to the
                # painted photo dest (not the widget/letterbox).
                lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                frame = overlay.scaled(
                    scaled.width(),
                    scaled.height(),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                lp.drawPixmap(x, y, frame)
            lp.end()
            painter.setOpacity(opacity)
            painter.drawPixmap(0, 0, layer)

        _draw(self._slide_at(self._index), self._cur_opacity)
        if self._nxt_opacity > 0.01:
            _draw(self._slide_at(self._next_index), self._nxt_opacity)
        painter.end()

    def _advance(self) -> None:
        if len(self._slides) < 2:
            return
        if self._fade is not None and self._fade.state() == QParallelAnimationGroup.State.Running:
            return

        self._next_index = (self._index + 1) % len(self._slides)
        nxt = self._slide_at(self._next_index)
        if nxt is not None:
            self._pixmap(str(nxt.get("image") or ""))
        after = self._slide_at((self._next_index + 1) % len(self._slides))
        if after is not None:
            self._pixmap(str(after.get("image") or ""))

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
            self.frame_changed.emit()
            current = self._slide_at(self._index)
            self._timer.start(_hold_ms_for(current or {"hold": 1.0}))

        group.finished.connect(_done)
        self._fade = group
        group.start()
