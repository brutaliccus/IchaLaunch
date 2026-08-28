"""Hairline frame with corner rivets, for art tiles.

ravencraft.io wraps every tile on its front page in a thin double border with a
small mark at each corner. It is most of why those cards read as one set rather
than a row of unrelated pictures, and the launcher had it on exactly one slide -
the featured one, which carries its own decorative PNG.

Painted rather than shipped as art so it sizes to any rect without a nine-slice,
and so the rivets stay square at every window size.
"""

from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QPainter, QPen

# The launcher's gold, carried at three weights: the outer line reads as the
# frame, the inner as its shadow, the rivets as hardware.
_LINE = QColor(241, 194, 45, 90)
_INNER = QColor(241, 194, 45, 42)
_RIVET = QColor(241, 194, 45, 165)
_RIVET_CORE = QColor(24, 19, 21, 200)

_INSET_PX = 3
_RIVET_PX = 6


def paint_rivet_frame(painter: QPainter, rect: QRect) -> None:
    """Draw the frame inside *rect*. Leaves the painter's state as it found it."""
    if rect.width() < 4 * _RIVET_PX or rect.height() < 4 * _RIVET_PX:
        return

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setBrush(QColor(0, 0, 0, 0))

    # drawRect covers [left, left + width], so the far edges need one back off
    # the rect or the line lands outside it and the mask clips it away.
    outer = rect.adjusted(0, 0, -1, -1)
    painter.setPen(QPen(_LINE, 1))
    painter.drawRect(outer)

    inner = outer.adjusted(_INSET_PX, _INSET_PX, -_INSET_PX, -_INSET_PX)
    if inner.width() > 0 and inner.height() > 0:
        painter.setPen(QPen(_INNER, 1))
        painter.drawRect(inner)

    painter.setPen(QPen(_RIVET, 1))
    for cx, cy in (
        (outer.left(), outer.top()),
        (outer.right(), outer.top()),
        (outer.left(), outer.bottom()),
        (outer.right(), outer.bottom()),
    ):
        half = _RIVET_PX // 2
        box = QRect(cx - half, cy - half, _RIVET_PX, _RIVET_PX)
        painter.fillRect(box, _RIVET)
        painter.fillRect(box.adjusted(2, 2, -2, -2), _RIVET_CORE)

    painter.restore()
