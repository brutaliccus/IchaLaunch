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

from PySide6.QtCore import Qt
from PySide6.QtGui import QLinearGradient, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QLabel, QWidget

# The site's two stops, read out of its own stylesheet rather than sampled.
GOLD_TOP = "#F1C22D"
GOLD_BOTTOM = "#FF7757"


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
