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

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QGradient,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QLabel, QWidget

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
    (0.00, "#c2531a"), (0.22, "#ff7a1f"), (0.42, "#ffc247"),
    (0.50, "#fff6d2"), (0.58, "#ffc247"), (0.78, "#ff7a1f"), (1.00, "#c2531a"),
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


def lava_rim_pixmap(silhouette: QPixmap, degrees: float) -> QPixmap:
    """The lava ramp swept round *silhouette*, masked to its shape."""
    out = QPixmap(silhouette.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    cone = QConicalGradient(out.rect().center(), degrees)
    for stop, hexc, alpha in LAVA_RIM:
        c = QColor(hexc)
        c.setAlpha(alpha)
        cone.setColorAt(stop, c)
    p.fillRect(out.rect(), cone)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.drawPixmap(0, 0, silhouette)
    p.end()
    return out


def lava_text_pen(rect: QRect, degrees: float) -> QPen:
    """A pen whose brush is the lava ramp travelling across *rect*."""
    span = max(1, rect.width())
    phase = (degrees / 360.0) * 2.0 * span
    ramp = QLinearGradient(rect.left() - span + phase, 0, rect.left() + phase, 0)
    for stop, hexc in LAVA_TEXT:
        ramp.setColorAt(stop, QColor(hexc))
    ramp.setSpread(QGradient.Spread.RepeatSpread)
    pen = QPen()
    pen.setBrush(ramp)
    return pen


class GradientLabel(QLabel):
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

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        text = self.text()
        if not text:
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(self.font())

        # Inside the style sheet's padding, so the ramp spans the ink and not
        # the box, and a padded label does not start mid-gradient.
        rect = self.contentsRect()
        ramp = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        ramp.setColorAt(0.0, self._top)
        ramp.setColorAt(1.0, self._bottom)

        pen = QPen()
        pen.setBrush(ramp)
        painter.setPen(pen)

        flags = int(self.alignment())
        if self.wordWrap():
            flags |= int(Qt.TextFlag.TextWordWrap)
        painter.drawText(rect, flags, text)
        painter.end()
