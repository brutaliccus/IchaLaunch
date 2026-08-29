"""A QLabel whose text is filled with a vertical gradient.

ravencraft.io does not paint its gold headings as a flat colour. It runs a
vertical gradient and clips it to the glyphs::

    background: linear-gradient(270deg, #F1C22D 0%, #FF7757 100%);
    -webkit-background-clip: text;

Qt style sheets cannot express that. ``color:`` takes a colour, not a brush, and
``qlineargradient`` in a ``color`` declaration is ignored. The only way to get
the same result is to paint the text with a gradient brush, which is what this
does: same two stops, same direction, applied down the text rectangle so every
line of a wrapped heading shares one ramp rather than repeating it per line.

Everything else about the label is untouched, so alignment, wrapping, font and
padding still come from the style sheet.
"""

from __future__ import annotations

import math
import zlib

from PySide6.QtCore import QObject, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QConicalGradient,
    QGradient,
    QImage,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QLabel, QWidget

from ichalaunch.ui.theme_fonts import ink_centered_rect

# The site's two stops, read out of its own stylesheet rather than sampled.
GOLD_TOP = "#F1C22D"
GOLD_BOTTOM = "#FF7757"


def gold_ramp(rect) -> QLinearGradient:
    """The site's vertical gold ramp, spanning *rect* top to bottom."""
    ramp = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
    ramp.setColorAt(0.0, GOLD_TOP)
    ramp.setColorAt(1.0, GOLD_BOTTOM)
    return ramp


def gold_pen(rect) -> QPen:
    """A pen that paints text with the ramp instead of a flat colour.

    Shared by the nav tabs and the launch plate so all three places that show a
    gold label agree, rather than each carrying its own pair of stops.
    """
    pen = QPen()
    pen.setBrush(gold_ramp(rect))
    return pen


# ---------------------------------------------------------------------------
# The molten hover treatment, shared by the launch plate and the nav tabs so the
# two cannot drift apart. Two ramps for two jobs.
#
# The RIM ramp fades to nothing at both ends: it is swept as a conical gradient
# and masked by a plate's own silhouette, so the heat runs round the real
# outline and dies away rather than closing into a ring.
#
# The TEXT ramp is opaque throughout and swept LINEARLY. A cone is wrong for a
# word, because its centre lands inside the text and the colour radiates out of
# one letter. Animating a linear ramp across the glyphs is what the CSS this
# came from actually does, and a label has to stay readable at every phase.
# ---------------------------------------------------------------------------
LAVA_RIM = (
    (0.00, "#5e1000", 0), (0.10, "#5e1000", 255), (0.20, "#b8330a", 255),
    (0.28, "#ff6a18", 255), (0.34, "#ffc247", 255), (0.38, "#ffe9a8", 255),
    (0.42, "#ffc247", 255), (0.50, "#ff5a1f", 255), (0.62, "#5e1000", 255),
    (0.76, "#5e1000", 0), (1.00, "#5e1000", 0),
)
LAVA_TEXT = (
    (0.00, "#d4611f"), (0.22, "#ff7a1f"), (0.42, "#ffc247"),
    (0.50, "#fff6d2"), (0.58, "#ffc247"), (0.78, "#ff7a1f"), (1.00, "#d4611f"),
)

SWEEP_MS = 33
SWEEP_PERIOD_MS = 4200


_HALO_CACHE: dict = {}
HALO_PAD = 16
HALO_BLUR = 6


def soft_halo(source: QPixmap, tint: str, pad: int = HALO_PAD, blur: int = HALO_BLUR) -> QPixmap:
    """*source*'s silhouette, tinted and blurred, so a glow FADES rather than ends.

    Drawing a silhouette several times at growing size with falling opacity is
    the cheap way to fake this, and it does not work: every copy keeps the hard
    edge, so the result is a stack of concentric outlines with a visible outer
    boundary. Blurring the alpha properly is what makes light fall off into the
    background.

    Three box-blur passes stand in for a Gaussian, which at this size is not a
    distinction anyone can see. Cached per source pixmap, because it is far too
    slow to do on every paint and never changes once built.
    """
    key = (source.cacheKey(), source.width(), source.height(), tint, pad, blur)
    hit = _HALO_CACHE.get(key)
    if hit is not None:
        return hit

    import numpy as np
    from PySide6.QtGui import QImage

    w, h = source.width() + pad * 2, source.height() + pad * 2
    canvas = QImage(w, h, QImage.Format.Format_ARGB32)
    canvas.fill(0)
    cp = QPainter(canvas)
    cp.drawPixmap(pad, pad, source)
    cp.end()

    arr = np.frombuffer(canvas.constBits(), dtype=np.uint8).reshape(h, canvas.bytesPerLine())
    arr = arr[:, : w * 4].reshape(h, w, 4).copy()
    alpha = arr[:, :, 3].astype(np.float32)

    k = max(1, blur)
    for _ in range(3):
        pa = np.pad(alpha, ((0, 0), (k, k)), mode="constant")
        cs = np.cumsum(pa, axis=1)
        alpha = (cs[:, 2 * k :] - cs[:, : -2 * k]) / (2.0 * k)
        pa = np.pad(alpha, ((k, k), (0, 0)), mode="constant")
        cs = np.cumsum(pa, axis=0)
        alpha = (cs[2 * k :, :] - cs[: -2 * k, :]) / (2.0 * k)

    c = QColor(tint)
    arr[:, :, 0] = c.blue()
    arr[:, :, 1] = c.green()
    arr[:, :, 2] = c.red()
    arr[:, :, 3] = np.clip(alpha, 0, 255).astype(np.uint8)
    out = QPixmap.fromImage(
        QImage(arr.tobytes(), w, h, w * 4, QImage.Format.Format_ARGB32).copy()
    )
    _HALO_CACHE[key] = out
    return out


def lava_flicker(phase_deg: float) -> float:
    """A soft 0.72 to 1.0 brightness wobble, for the torch feel.

    Three sines at frequencies that do not divide into each other, so the sum
    never repeats on a beat you can hear. Deterministic rather than random: a
    random walk on every frame reads as noise or as a fault, while this reads as
    a flame being pushed about by air. The floor is well above zero because a
    light that goes fully dark reads as a bug, not as a flicker.
    """
    r = math.radians(phase_deg)
    wobble = (
        0.55 * math.sin(r * 3.0)
        + 0.30 * math.sin(r * 7.3 + 1.1)
        + 0.15 * math.sin(r * 13.7 + 2.7)
    )
    return 0.86 + 0.14 * wobble


# Per-contributor rim ramps. Same shape as LAVA_RIM: fade in, burn, fade out,
# then a long dead arc so only part of the outline is lit at a time.
def _ramp(*hexes):
    n = len(hexes)
    stops = []
    for i, h in enumerate(hexes):
        pos = 0.62 * i / max(1, n - 1)
        stops.append((pos, h, 0 if i in (0, n - 1) else 255))
    stops.append((0.80, hexes[-1], 0))
    stops.append((1.00, hexes[-1], 0))
    return tuple(stops)


# Mynie: neon. Saturated hot pink, the chroma of signage rather than of skin.
RIM_NEON_PINK = _ramp("#3d0022", "#a8005f", "#ff1d8e", "#ff6ec4", "#ffd0ea", "#ff1d8e", "#3d0022")
# Valheru: pink washing to white. Paler and softer, heated metal not neon.
RIM_PINK_WHITE = _ramp("#3a1c26", "#b4707f", "#f2a8bd", "#ffd9e4", "#fffafc", "#f2a8bd", "#3a1c26")
# Ordinary Joe: electric, frosty. Deliberately the cold one.
RIM_FROST_BLUE = _ramp("#00203a", "#0d6ea8", "#31b6ef", "#8ee2ff", "#eafaff", "#31b6ef", "#00203a")


def lava_rim_pixmap(silhouette: QPixmap, degrees: float, ramp=None) -> QPixmap:
    """The lava ramp swept round *silhouette*, masked to its shape.

    Pass a BLURRED silhouette to get a soft rim. The gradient itself is smooth,
    so the only hard edge in the result comes from the mask: masking with the
    sharp plate produced a crisp band of lava with a cut-off outline, which is
    the one thing this effect must not look like.
    """
    out = QPixmap(silhouette.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    cone = QConicalGradient(out.rect().center(), degrees)
    for stop, hexc, alpha in (ramp or LAVA_RIM):
        c = QColor(hexc)
        c.setAlpha(alpha)
        cone.setColorAt(stop, c)
    p.fillRect(out.rect(), cone)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.drawPixmap(0, 0, silhouette)
    p.end()
    return out


def sample_ramp_from_pixmap(source: QPixmap, want: int = 3) -> tuple:
    """Build a text ramp from the dominant saturated colours of *source*.

    Sampled rather than hardcoded so a portrait and its tooltip cannot disagree,
    and so swapping the art retints the text with no code change.

    Near-black and near-white are dropped before counting: every portrait is
    mostly shadow and highlight, and those would win every time while telling
    you nothing about the picture. What survives is quantised coarsely, ranked
    by saturation times frequency, and then LIGHTNESS CLAMPED, because a sampled
    colour can land anywhere and this has to stay readable on a dark tooltip. A
    colour too dark to read is lifted until it clears roughly 4.5:1.
    """
    import numpy as np

    img = source.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    if w < 2 or h < 2:
        return LAVA_TEXT
    arr = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(h, img.bytesPerLine())
    arr = arr[:, : w * 4].reshape(h, w, 4)
    opaque = arr[arr[:, :, 3] > 200][:, :3][:, ::-1]          # BGRA -> RGB
    if opaque.size == 0:
        return LAVA_TEXT

    mx = opaque.max(axis=1).astype(np.int16)
    mn = opaque.min(axis=1).astype(np.int16)
    chroma = (mx - mn)

    # A MONOCHROME portrait must not be forced through the colour path. Dropping
    # greys is right for a colour picture, where black and white are just shadow
    # and highlight, but a greyscale portrait IS greys, and filtering them out
    # leaves only stray pixels from the frame around it. When the art carries
    # almost no chroma, build the ramp from its tonal range instead, so black,
    # grey and white are the palette rather than the thing being discarded.
    lum = (0.2126 * opaque[:, 0] + 0.7152 * opaque[:, 1] + 0.0722 * opaque[:, 2])
    if float(np.median(chroma)) < 18.0:
        lo, mid, hi = (float(np.percentile(lum, p)) for p in (22, 55, 92))
        def _grey(v):
            v = int(max(0, min(255, v)))
            c = QColor(v, v, v)
            while _rel_lum(c) < 0.24 and c.value() < 252:
                v = min(255, v + 8)
                c = QColor(v, v, v)
            return c.name()
        dark, middle, light = _grey(lo), _grey(mid), _grey(hi)
        return (
            (0.00, dark), (0.22, middle), (0.42, light),
            (0.50, "#ffffff"), (0.58, light),
            (0.78, middle), (1.00, dark),
        )

    keep = (mx > 55) & (mx < 246) & (chroma > 28)             # drop black, white, grey
    px = opaque[keep]
    if len(px) < 24:
        return LAVA_TEXT

    q = (px // 32) * 32
    keys, counts = np.unique(q, axis=0, return_counts=True)
    kmx = keys.max(axis=1).astype(np.int16)
    kmn = keys.min(axis=1).astype(np.int16)
    score = (kmx - kmn).astype(np.float64) * np.sqrt(counts)
    order = np.argsort(-score)[: max(2, want)]
    picks = [QColor(int(keys[i][0]), int(keys[i][1]), int(keys[i][2])) for i in order]

    lifted = []
    for c in picks:
        hsv = QColor(c)
        while _rel_lum(hsv) < 0.24 and hsv.value() < 252:      # clears 4.5:1 on a dark tip
            hsv.setHsv(hsv.hue(), hsv.saturation(), min(255, hsv.value() + 12))
        # Value alone cannot save a saturated blue: blue carries only 7 percent
        # of luminance, so a full-value pure blue still measures about 3.4:1 on
        # near-black. The only way up is toward white, so desaturate until it
        # clears. Hue is preserved, which is why the result reads as a frosty
        # pale blue rather than as a different colour.
        while _rel_lum(hsv) < 0.24 and hsv.saturation() > 0:
            hsv.setHsv(hsv.hue(), max(0, hsv.saturation() - 14), 255)
        lifted.append(hsv)
    lifted.sort(key=_rel_lum)

    dark = lifted[0].name()
    mid = lifted[len(lifted) // 2].name()
    hot = QColor(lifted[-1])
    hot.setHsv(hot.hue(), max(0, hot.saturation() - 90), 255)  # a white-hot crest
    return (
        (0.00, dark), (0.22, mid), (0.42, lifted[-1].name()),
        (0.50, hot.name()), (0.58, lifted[-1].name()),
        (0.78, mid), (1.00, dark),
    )


def _rel_lum(c: QColor) -> float:
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def lava_text_pen(rect: QRect, degrees: float, stops=None) -> QPen:
    """A pen whose brush is a ramp travelling across *rect*."""
    span = max(1, rect.width())
    phase = (degrees / 360.0) * 2.0 * span
    ramp = QLinearGradient(rect.left() - span + phase, 0, rect.left() + phase, 0)
    for stop, hexc in (stops or LAVA_TEXT):
        ramp.setColorAt(stop, QColor(hexc))
    ramp.setSpread(QGradient.Spread.RepeatSpread)
    pen = QPen()
    pen.setBrush(ramp)
    return pen


def lava_ink_rect(box: QRect, font: QFont, text: str, flags: int) -> QRect:
    """Tight glyph box of *text* as ``drawText(box, flags, text)`` would place it.

    ``ink_centered_rect`` only shifts a layout box; it keeps the full width and
    height. Filling that box (or that box plus a glow pad) is a lava rectangle
    that hangs past the letters and past tab chrome. This is the ink itself.
    """
    if not text or not box.isValid():
        return QRect()
    fm = QFontMetrics(font)
    laid = fm.boundingRect(box, flags, text)
    if laid.isEmpty():
        return QRect()
    tight = fm.tightBoundingRect(text)
    loose = fm.boundingRect(text)
    ink = QRect(
        laid.x() + (tight.x() - loose.x()),
        laid.y() + (tight.y() - loose.y()),
        max(1, tight.width()),
        max(1, tight.height()),
    )
    # One-pixel fringe for antialiasing; stay inside the layout box.
    return ink.adjusted(-1, -1, 1, 1).intersected(box)


def paint_lava_glyphs(
    painter: QPainter,
    box: QRect,
    text: str,
    flags: int,
    degrees: float,
    *,
    font: QFont | None = None,
    stops=None,
    clip: QRect | None = None,
) -> None:
    """Fill *text* with the travelling lava ramp, glyph strokes only.

    Same path as PLAY: a gradient pen and ``drawText``. An offscreen fill of
    the tight ink box, SourceIn, then ``drawPixmap`` is what painted a solid
    gold bar in front of the tab label — Windows QPixmap often has no alpha,
    so the blit is an opaque rectangle the size of the word.
    """
    if not text or not box.isValid():
        return
    if font is not None:
        painter.setFont(font)
    font = painter.font()
    ink = lava_ink_rect(box, font, text, flags)
    if clip is not None:
        ink = ink.intersected(clip)
    ramp_box = ink if ink.isValid() and not ink.isEmpty() else box
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        if clip is not None:
            painter.setClipRect(clip, Qt.ClipOperation.IntersectClip)
        else:
            painter.setClipRect(box, Qt.ClipOperation.IntersectClip)
        painter.setPen(lava_text_pen(ramp_box, degrees, stops))
        painter.drawText(box, flags, text)
    finally:
        painter.restore()


# ---------------------------------------------------------------------------
# ONE phase for every animated heading in the window.
#
# The alternative is a timer per label, and with a dozen headings that means a
# dozen timers firing on their own schedules, so the ramps drift apart and the
# window looks like it is made of unrelated parts. A single ticker keeps them
# moving as one surface. It also runs only while something visible is
# subscribed, so switching tabs stops the work for the headings that went away.
# ---------------------------------------------------------------------------
HEADING_PERIOD_MS = 9000   # deliberately slower than the hover sweep


class _LavaTicker(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.phase = 0.0
        self._subs: set = set()
        self._timer = QTimer(self)
        self._timer.setInterval(SWEEP_MS)
        self._timer.timeout.connect(self._tick)

    def subscribe(self, w) -> None:
        self._subs.add(w)
        if not self._timer.isActive():
            self._timer.start()

    def unsubscribe(self, w) -> None:
        self._subs.discard(w)
        if not self._subs:
            self._timer.stop()

    def _tick(self) -> None:
        self.phase = (self.phase + 360.0 * SWEEP_MS / HEADING_PERIOD_MS) % 360.0
        dead = []
        for w in self._subs:
            try:
                if w.isVisible():
                    w.update()
            except RuntimeError:      # C++ side already gone
                dead.append(w)
        for w in dead:
            self._subs.discard(w)
        if not self._subs:
            self._timer.stop()


_TICKER: "_LavaTicker | None" = None


def phase_seed(text: str) -> tuple:
    """A stable per-label (offset, rate) into the shared clock.

    Sharing the ticker is right, a timer each would drift and cost more. Sharing
    the PHASE is not: labels then sweep in lockstep and read as one bar of light
    crossing them. Offsets fix that, but equal periods with fixed offsets still
    march once the eye finds the pattern, so the rates are deliberately
    incommensurate. Same principle as the torch flicker's non-dividing sines.

    crc32, not hash(): Python randomises string hashing per process, so hash()
    would reshuffle on every launch and a label would never look like itself
    twice.
    """
    seed = zlib.crc32((text or "").encode("utf-8"))
    return (seed % 360), 0.72 + ((seed >> 9) % 61) / 100.0


def lava_ticker() -> "_LavaTicker":
    global _TICKER
    if _TICKER is None:
        _TICKER = _LavaTicker()
    return _TICKER


class AnimatedLavaLabel(QLabel):
    """A label whose text carries the travelling lava ramp.

    Reserved for things that MOVE FOR A REASON. The section headings used this
    briefly and it was too much: with a dozen labels animating, motion stopped
    meaning anything, so they went back to the static gradient and only
    hover-driven chrome still moves. Per-label phase offsets went with them,
    since their only job was stopping a column of headings marching in step.

    Repaints from the shared ticker rather than a timer of its own, so every
    heading in the window moves on one phase. Painting reads ``self.text()`` at
    paint time, so a label whose string changes later, like the play-bar status,
    keeps the effect on whatever it currently says.

    ``set_plain(colour)`` drops a single label out of the effect, for the case
    where shimmering would be wrong.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._plain: "str | None" = None
        self._phase_offset, self._phase_rate = phase_seed(text)

    def own_phase(self) -> float:
        return (lava_ticker().phase * self._phase_rate + self._phase_offset) % 360.0

    def set_plain(self, colour: "str | None") -> None:
        self._plain = colour
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        lava_ticker().subscribe(self)

    def hideEvent(self, event) -> None:  # noqa: N802
        lava_ticker().unsubscribe(self)
        super().hideEvent(event)

    def _fitted_font(self, rect) -> QFont:
        """Shrink the type until the string fits its box.

        The stylesheet pins this at a size chosen to match the launch label,
        which is right for a short word like Ready and wrong for
        "Installing update and restarting...". Pinning a size and letting long
        strings clip is the bug; fitting keeps the intended size whenever the
        string is short enough to have it.
        """
        font = QFont(self.font())
        base = font.pixelSize() if font.pixelSize() > 0 else 16
        text = self.text()
        for px in range(base, 9, -1):
            font.setPixelSize(px)
            fm = QFontMetrics(font)
            ink = max(fm.height(), fm.tightBoundingRect(text).height())
            if fm.horizontalAdvance(text) <= rect.width() and ink <= rect.height():
                return font
        font.setPixelSize(10)
        return font

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        text = self.text()
        if not text:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setFont(self._fitted_font(self.contentsRect()))
            rect = self.contentsRect()
            if self._plain:
                painter.setPen(QColor(self._plain))
            else:
                painter.setPen(lava_text_pen(rect, self.own_phase()))
            flags = int(self.alignment())
            if self.wordWrap():
                flags |= int(Qt.TextFlag.TextWordWrap)
            painter.drawText(ink_centered_rect(rect, painter.font(), text), flags, text)
        finally:
            if painter.isActive():
                painter.end()


class GradientLabel(QLabel):
    """Text filled with the site's static gold ramp, fitted to its box.

    Fitting lives here rather than on the animated subclass because the status
    line is static now and still has to cope with strings four times longer
    than "Ready". A pinned size that clips long text is the bug; this keeps the
    intended size whenever the string is short enough to have it.
    """

    def _fitted_font(self, rect) -> QFont:
        """Shrink only when the string genuinely cannot fit its box.

        Wrap-aware on purpose. Measuring a wrapped label by one-line advance
        shrinks "Checking addon & client updates…" to unreadable when two lines
        at full size would have fit, which is the opposite of the bug it is
        meant to solve.
        """
        font = QFont(self.font())
        base = font.pixelSize() if font.pixelSize() > 0 else 16
        text = self.text()
        if not text:
            return font
        wrap = self.wordWrap()
        flags = int(self.alignment())
        if wrap:
            flags |= int(Qt.TextFlag.TextWordWrap)
        for px in range(base, 9, -1):
            font.setPixelSize(px)
            fm = QFontMetrics(font)
            if wrap:
                box = fm.boundingRect(QRect(0, 0, max(rect.width(), 1), 1 << 20), flags, text)
                if box.height() <= rect.height():
                    return font
            else:
                ink = max(fm.height(), fm.tightBoundingRect(text).height())
                if fm.horizontalAdvance(text) <= rect.width() and ink <= rect.height():
                    return font
        font.setPixelSize(10)
        return font


    """Draws its text with the site's vertical gold ramp."""

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        top: str = GOLD_TOP,
        bottom: str = GOLD_BOTTOM,
    ) -> None:
        super().__init__(text, parent)
        self._top = top
        self._bottom = bottom
        self._plain: "str | None" = None

    def set_plain(self, colour: "str | None") -> None:
        """Drop this label out of the ramp, e.g. so an error does not shimmer.

        Parity with AnimatedLavaLabel. set_status() calls this on every status
        change; without it the play-bar status raised AttributeError and killed
        the rest of the caller, including the update-check worker.
        """
        self._plain = colour
        self.update()

    def sizeHint(self) -> QSize:
        """Ask for the width the string actually needs.

        A word-wrapped QLabel reports a narrow hint, so at stretch 0 the play
        bar handed the status line about 130px and a long boot message wrapped
        to three lines, overflowing the 86px bar. The maximumWidth cap already
        allowed 420 and nothing was ever claiming it: width was being refused
        before size was. Qt clamps this between minimumWidth and maximumWidth,
        so short text still occupies the old footprint.
        """
        text = self.text()
        if not text:
            return super().sizeHint()
        fm = QFontMetrics(self.font())
        m = self.contentsMargins()
        # +2 vertical headroom: the fit test rejects a size whose ink is even a
        # pixel taller than the box, and this face measures one over its own
        # line height. Without the slack "Ready" drops to 19px and stops
        # matching the launch plate it is supposed to sit level with.
        return QSize(
            fm.horizontalAdvance(text) + m.left() + m.right() + 2,
            max(fm.height(), fm.tightBoundingRect(text).height()) + m.top() + m.bottom() + 2,
        )

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        """Height including the same 2px the size hint reserves.

        A word-wrapped QLabel advertises heightForWidth, and the layout prefers
        it over sizeHint().height(). Leaving it on the base implementation put
        the headroom in the hint and then threw it away, so the fit test still
        saw a box one pixel short and dropped a size.
        """
        text = self.text()
        if not text:
            return super().heightForWidth(width)
        fm = QFontMetrics(self.font())
        m = self.contentsMargins()
        flags = int(self.alignment())
        if self.wordWrap():
            flags |= int(Qt.TextFlag.TextWordWrap)
        inner = max(width - m.left() - m.right(), 1)
        box = fm.boundingRect(QRect(0, 0, inner, 1 << 20), flags, text)
        ink = max(box.height(), fm.tightBoundingRect(text).height())
        return ink + m.top() + m.bottom() + 2

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        text = self.text()
        if not text:
            super().paintEvent(event)
            return

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setFont(self._fitted_font(self.contentsRect()))

            # Inside the style sheet's padding, so the ramp spans the ink and not
            # the box, and a padded label does not start mid-gradient.
            rect = self.contentsRect()
            ramp = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
            ramp.setColorAt(0.0, self._top)
            ramp.setColorAt(1.0, self._bottom)

            pen = QPen()
            pen.setBrush(ramp)
            painter.setPen(pen)
            if self._plain:
                painter.setPen(QColor(self._plain))

            flags = int(self.alignment())
            if self.wordWrap():
                flags |= int(Qt.TextFlag.TextWordWrap)
            # Single line only: this face sets its capitals high inside a tall
            # ascent, so Qt's line-box centring shears the tops. Multi-line text is
            # left alone, where the same shift would push the last line out.
            draw = rect
            if not (flags & int(Qt.TextFlag.TextWordWrap)):
                draw = ink_centered_rect(rect, painter.font(), text)
            painter.drawText(draw, flags, text)
        finally:
            if painter.isActive():
                painter.end()
